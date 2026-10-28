"""Who is using the web pages?

* On the host PC (127.0.0.1:8788): the owner, full access, after the owner password (owner_auth.py).
* From other laptops via the public address: Cloudflare Access does the email login;
  we verify its signed token ourselves (so a missing Access policy never exposes the
  pages) and map the email to an admin or supervisor in the config.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import Config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class User:
    email: str
    name: str
    role: str          # owner | supervisor | admin
    key: str           # admin/supervisor key, "owner" for the host PC

    @property
    def can_manage_templates(self) -> bool:
        return self.role in ("owner", "supervisor")


OWNER = User("", "Pemilik (komputer ini)", "owner", "owner")


class AccessVerifier:
    MAX_AGE_SECONDS = 12 * 3600     # refuse logins older than this even if Cloudflare still accepts them

    def __init__(self, team_domain: str, audience: str):
        import jwt
        self.team = team_domain.removeprefix("https://").strip("/")
        self.audience = audience
        self.jwks = jwt.PyJWKClient(f"https://{self.team}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)

    def email(self, token: str) -> str | None:
        import jwt
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience,
                                issuer=f"https://{self.team}", options={"require": ["exp", "iat", "aud", "iss"]})
            if time.time() - int(claims["iat"]) > self.MAX_AGE_SECONDS:
                log.warning("Cloudflare Access token too old; login again")
                return None
        except Exception as e:  # any invalid/expired/unverifiable token = no access
            log.warning("Cloudflare Access token rejected: %s", type(e).__name__)
            return None
        return (claims.get("email") or "").lower() or None


def user_for_email(cfg: Config, email: str) -> User | None:
    email = email.lower()
    for s in cfg.supervisors:
        if s.email and s.email.lower() == email:
            return User(email, s.name, "supervisor", s.key)
    for a in cfg.admins:
        if a.email and a.email.lower() == email:
            return User(email, a.name, "admin", a.key)
    return None


def remote_user(request: Request, cfg: Config, verifier: AccessVerifier | None) -> User:
    if verifier is None:
        raise HTTPException(403, "Login belum diatur. Buka halaman ini dari komputer server, "
                                 "atau atur Cloudflare Access di Pengaturan.")
    token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization")
    email = verifier.email(token) if token else None
    if not email:
        raise HTTPException(401, "Silakan login lewat Cloudflare Access.")
    user = user_for_email(cfg, email)
    if not user:
        raise HTTPException(403, f"{email} belum terdaftar sebagai admin/supervisor di Pengaturan.")
    return user
