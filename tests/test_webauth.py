import dataclasses
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from heroez_ops.config import Admin, Supervisor
from heroez_ops.webauth import AccessVerifier, remote_user, user_for_email

TEAM, AUD = "heroez.cloudflareaccess.com", "aud-tag-123"


@pytest.fixture
def keys(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJWKS:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": private.public_key()})()
    v = AccessVerifier(TEAM, AUD)
    v.jwks = FakeJWKS()
    return v, private, other


def token(key, **over):
    claims = {"email": "Rina@Heroez.id", "aud": AUD, "iss": f"https://{TEAM}", "exp": int(time.time()) + 600, **over}
    return jwt.encode(claims, key, algorithm="RS256")


def test_valid_token_maps_to_admin(cfg, keys):
    v, private, _ = keys
    c = dataclasses.replace(cfg, admins=(Admin("rina", "Rina", 1001, email="rina@heroez.id"),),
                            supervisors=(Supervisor("sari", "Sari", 2001, email="sari@heroez.id"),))
    req = type("R", (), {"headers": {"cf-access-jwt-assertion": token(private)}, "cookies": {}})()
    u = remote_user(req, c, v)
    assert (u.role, u.key) == ("admin", "rina") and not u.can_manage_templates
    assert user_for_email(c, "SARI@heroez.id").can_manage_templates


@pytest.mark.parametrize("bad", [{"aud": "other"}, {"iss": "https://evil.cloudflareaccess.com"},
                                 {"exp": int(time.time()) - 10}])
def test_bad_tokens_rejected(keys, bad):
    v, private, _ = keys
    assert v.email(token(private, **bad)) is None


def test_wrong_signature_rejected(keys):
    v, _, other = keys
    assert v.email(token(other)) is None


def test_unknown_email_refused(cfg, keys):
    v, private, _ = keys
    req = type("R", (), {"headers": {"cf-access-jwt-assertion": token(private, email="stranger@gmail.com")}, "cookies": {}})()
    with pytest.raises(HTTPException) as ex:
        remote_user(req, cfg, v)
    assert ex.value.status_code == 403
