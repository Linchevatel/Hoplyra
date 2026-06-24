from __future__ import annotations

import hashlib
import os
import secrets as pysecrets

from hoplyra.db import _now, connect

DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "admin"
_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt = salt or pysecrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    )
    return f"pbkdf2-sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, hexdigest = stored.split("$", 3)
        if scheme != "pbkdf2-sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        )
        return pysecrets.compare_digest(digest.hex(), hexdigest)
    except (ValueError, TypeError):
        return False


def ensure_default_admin() -> None:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM panel_auth LIMIT 1").fetchone()
        if row:
            return
        user = os.environ.get("HOPLYRA_ADMIN_USER", DEFAULT_USER).strip() or DEFAULT_USER
        password = os.environ.get("HOPLYRA_ADMIN_PASSWORD", DEFAULT_PASSWORD).strip() or DEFAULT_PASSWORD
        conn.execute(
            "INSERT INTO panel_auth (username, password_hash, updated_at) VALUES (?, ?, ?)",
            (user, hash_password(password), _now()),
        )


def get_admin() -> tuple[str, str] | None:
    with connect() as conn:
        row = conn.execute("SELECT username, password_hash FROM panel_auth LIMIT 1").fetchone()
    if not row:
        return None
    return row["username"], row["password_hash"]


def admin_username() -> str:
    admin = get_admin()
    return admin[0] if admin else DEFAULT_USER


def verify_admin_credentials(username: str, password: str) -> bool:
    admin = get_admin()
    if not admin or not username or not password:
        return False
    expected_user, password_hash = admin
    return pysecrets.compare_digest(username.strip(), expected_user) and verify_password(
        password, password_hash
    )


def change_admin_password(current_password: str, new_password: str) -> None:
    admin = get_admin()
    if not admin:
        raise ValueError("Учётная запись не найдена")
    username, password_hash = admin
    if not verify_password(current_password, password_hash):
        raise ValueError("Неверный текущий пароль")
    new_password = new_password.strip()
    if len(new_password) < 4:
        raise ValueError("Новый пароль должен быть не короче 4 символов")
    if verify_password(new_password, password_hash):
        raise ValueError("Новый пароль совпадает с текущим")
    with connect() as conn:
        conn.execute(
            "UPDATE panel_auth SET password_hash=?, updated_at=? WHERE username=?",
            (hash_password(new_password), _now(), username),
        )


def is_default_password() -> bool:
    admin = get_admin()
    if not admin:
        return True
    username, password_hash = admin
    return username == DEFAULT_USER and verify_password(DEFAULT_PASSWORD, password_hash)
