from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_ENC_PREFIX = "enc:v1:"


def _fernet() -> Fernet | None:
    raw = os.environ.get("HOPLYRA_SECRET_KEY", "").strip()
    if not raw:
        return None
    try:
        return Fernet(raw.encode())
    except Exception:
        digest = hashlib.sha256(raw.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
        return Fernet(key)


def encrypt_auth_secret(plaintext: str) -> str:
    f = _fernet()
    if f is None:
        return plaintext
    token = f.encrypt(plaintext.encode()).decode()
    return f"{_ENC_PREFIX}{token}"


def decrypt_auth_secret(stored: str | None) -> str | None:
    if stored is None:
        return None
    if not stored.startswith(_ENC_PREFIX):
        return stored
    f = _fernet()
    if f is None:
        raise RuntimeError("HOPLYRA_SECRET_KEY required to decrypt stored credentials")
    try:
        return f.decrypt(stored[len(_ENC_PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Failed to decrypt auth_secret") from exc


def is_encrypted(stored: str | None) -> bool:
    return bool(stored and stored.startswith(_ENC_PREFIX))
