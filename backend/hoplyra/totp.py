from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse


def generate_totp_secret() -> str:
    """Generate a high-entropy 160-bit Base32 secret without padding."""
    return base64.b32encode(secrets.token_bytes(20)).decode("utf-8").rstrip("=")


def build_otpauth_uri(secret: str, username: str = "admin", issuer: str = "Hoplyra") -> str:
    """Build standard otpauth:// URI for authenticator apps."""
    label = f"{issuer}:{username}"
    params = {
        "secret": secret.upper(),
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": "6",
        "period": "30",
    }
    return f"otpauth://totp/{urllib.parse.quote(label)}?{urllib.parse.urlencode(params)}"


def verify_totp(secret: str, code: str, *, window: int = 1, min_step: int | None = None) -> tuple[bool, int | None]:
    """Verify a 6-digit TOTP code against secret with drift window (+-1 step = 30s).
    Returns (True, matched_step) if valid and matched_step > min_step, else (False, None).
    """
    clean_code = str(code).strip()
    if len(clean_code) != 6 or not clean_code.isdigit():
        return False, None

    secret_clean = secret.strip().upper()
    missing_padding = len(secret_clean) % 8
    if missing_padding:
        secret_clean += "=" * (8 - missing_padding)

    try:
        key = base64.b32decode(secret_clean)
    except Exception:
        return False, None

    current_interval = int(time.time() // 30)
    for drift in range(-window, window + 1):
        counter = current_interval + drift
        if min_step is not None and counter <= min_step:
            continue
        msg = struct.pack(">Q", counter)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        offset = h[-1] & 0x0F
        truncated = struct.unpack(">I", h[offset : offset + 4])[0] & 0x7FFFFFFF
        computed = str(truncated % 1_000_000).zfill(6)
        if hmac.compare_digest(computed, clean_code):
            return True, counter
    return False, None
