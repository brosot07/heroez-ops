"""Secrets file (bot token, Meta app secret, tunnel token, API keys).

The keys are typed into the settings page; nobody handles a .env file.
* Windows: encrypted with DPAPI (machine-bound; the service runs as LocalSystem,
  so only that account can decrypt it).
* Linux server: encrypted with AES-256-GCM. The key is `master.key` next to the file
  (0600). Keep it out of backups that leave the server; without it the file is useless.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

KEYS = ("META_APP_SECRET", "META_VERIFY_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "CLOUDFLARE_TUNNEL_TOKEN", "ANTHROPIC_API_KEY", "META_ACCESS_TOKEN", "URLHAUS_AUTH_KEY", "DEALS_SHEET_TOKEN")
_MAGIC = b"HZDPAPI1"
_AES_MAGIC = b"HZAESGCM1"


def _master_key(path: Path) -> bytes:
    kp = path.parent / "master.key"
    if kp.exists():
        key = bytes.fromhex(kp.read_text(encoding="utf-8").strip())
        if len(key) != 32:
            raise ValueError(f"{kp} is not a 256-bit key")
        return key
    key = os.urandom(32)
    kp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key.hex() + "\n")
    return key


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = Blob(len(data), buf), Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def load(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = path.read_bytes()
    if raw.startswith(_MAGIC):
        raw = _dpapi(raw[len(_MAGIC):], protect=False)
    elif raw.startswith(_AES_MAGIC):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        body = raw[len(_AES_MAGIC):]
        raw = AESGCM(_master_key(path)).decrypt(body[:12], body[12:], _AES_MAGIC)
    out = {}
    for line in raw.decode("utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() in KEYS:
            out[k.strip()] = v.strip()
    return out


def save(path: Path, values: dict[str, str]) -> None:
    data = "".join(f"{k}={values[k]}\n" for k in KEYS if values.get(k)).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        data = _MAGIC + _dpapi(data, protect=True)
    else:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        data = _AES_MAGIC + nonce + AESGCM(_master_key(path)).encrypt(nonce, data, _AES_MAGIC)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def apply_to_env(values: dict[str, str]) -> None:
    for k in KEYS:
        if values.get(k):
            os.environ[k] = values[k]
        else:
            os.environ.pop(k, None)
