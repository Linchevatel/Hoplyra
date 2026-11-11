from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def generate_wg_keypair() -> tuple[str, str]:
    private = X25519PrivateKey.generate()
    priv_bytes = private.private_bytes_raw()
    pub_bytes = private.public_key().public_bytes_raw()
    return (
        base64.b64encode(priv_bytes).decode(),
        base64.b64encode(pub_bytes).decode(),
    )


def generate_wg_psk() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()
