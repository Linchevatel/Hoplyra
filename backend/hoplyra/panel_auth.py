from __future__ import annotations

import hashlib
import os
import secrets as pysecrets
from typing import Any

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


def get_auth_snapshot(username: str = "admin") -> tuple[str, str, int, bool, str | None, int | None] | None:
    """Return an atomic snapshot of (username, password_hash, session_version, totp_enabled, totp_secret, totp_last_step)."""
    from hoplyra.secrets import decrypt_auth_secret

    with connect() as conn:
        row = conn.execute(
            "SELECT username, password_hash, session_version, totp_enabled, totp_secret, totp_last_step "
            "FROM panel_auth WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return None
        secret = decrypt_auth_secret(row["totp_secret"]) if row["totp_secret"] else None
        sv = int(row["session_version"]) if row["session_version"] is not None else 1
        last_step = int(row["totp_last_step"]) if row["totp_last_step"] is not None else None
        return (
            row["username"],
            row["password_hash"],
            sv,
            bool(row["totp_enabled"]),
            secret,
            last_step,
        )


def verify_admin_credentials(username: str, password: str) -> bool:
    admin = get_admin()
    if not admin or not username or not password:
        return False
    expected_user, password_hash = admin
    return pysecrets.compare_digest(username.strip(), expected_user) and verify_password(
        password, password_hash
    )


def get_session_version(username: str = "admin") -> int | None:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT session_version FROM panel_auth WHERE username = ?",
                (username,),
            ).fetchone()
        if row and row["session_version"] is not None:
            return int(row["session_version"])
    except Exception:
        pass
    return None


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
            "UPDATE panel_auth SET password_hash=?, updated_at=?, session_version=COALESCE(session_version, 1)+1 WHERE username=?",
            (hash_password(new_password), _now(), username),
        )


def is_default_password() -> bool:
    admin = get_admin()
    if not admin:
        return True
    username, password_hash = admin
    return username == DEFAULT_USER and verify_password(DEFAULT_PASSWORD, password_hash)


def is_totp_enabled(username: str = "admin") -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT totp_enabled FROM panel_auth WHERE username = ?",
            (username,),
        ).fetchone()
        if row and row["totp_enabled"]:
            return bool(row["totp_enabled"])
    return False


def get_totp_secret(username: str = "admin") -> str | None:
    from hoplyra.secrets import decrypt_auth_secret

    with connect() as conn:
        row = conn.execute(
            "SELECT totp_secret FROM panel_auth WHERE username = ?",
            (username,),
        ).fetchone()
        if row and row["totp_secret"]:
            return decrypt_auth_secret(row["totp_secret"])
    return None


def set_totp_secret_if_disabled(username: str, secret: str) -> bool:
    """Save secret only if 2FA is currently disabled. Returns True on success."""
    from hoplyra.secrets import encrypt_auth_secret

    with connect() as conn:
        cursor = conn.execute(
            "UPDATE panel_auth SET totp_secret = ?, updated_at = ? "
            "WHERE username = ? AND (totp_enabled = 0 OR totp_enabled IS NULL)",
            (encrypt_auth_secret(secret), _now(), username),
        )
        conn.commit()
        return cursor.rowcount > 0


def enable_totp_conditional(
    username: str,
    expected_secret: str,
    expected_sv: int,
    confirmed_step: int | None = None,
) -> tuple[bool, int | None]:
    """Enable 2FA only if the unconfirmed secret matches expected_secret,
    session_version matches expected_sv, and 2FA is still disabled.
    Returns (True, new_session_version) on success, else (False, None).
    """
    from hoplyra.secrets import decrypt_auth_secret

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT totp_secret, totp_enabled, session_version FROM panel_auth WHERE username = ?",
            (username,),
        ).fetchone()
        if not row or row["totp_enabled"]:
            return False, None
        current_sv = int(row["session_version"]) if row["session_version"] is not None else 1
        if current_sv != expected_sv:
            return False, None
        current_secret = decrypt_auth_secret(row["totp_secret"]) if row["totp_secret"] else None
        if not current_secret or current_secret != expected_secret:
            return False, None
        new_sv = current_sv + 1
        cursor = conn.execute(
            "UPDATE panel_auth SET totp_enabled = 1, updated_at = ?, session_version = ?, totp_last_step = ? "
            "WHERE username = ? AND (totp_enabled = 0 OR totp_enabled IS NULL) AND session_version = ?",
            (_now(), new_sv, confirmed_step, username, current_sv),
        )
        conn.commit()
        if cursor.rowcount > 0:
            return True, new_sv
        return False, None


def disable_totp_conditional(
    username: str,
    expected_secret: str,
    expected_sv: int,
    consumed_step: int | None = None,
) -> tuple[bool, int | None]:
    """Disable 2FA only if the current secret matches expected_secret,
    session_version matches expected_sv, 2FA is enabled, and consumed_step is valid/anti-replayed.
    Returns (True, new_session_version) on success, else (False, None).
    """
    from hoplyra.secrets import decrypt_auth_secret

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT totp_secret, totp_enabled, session_version, totp_last_step FROM panel_auth WHERE username = ?",
            (username,),
        ).fetchone()
        if not row or not row["totp_enabled"]:
            return False, None
        current_sv = int(row["session_version"]) if row["session_version"] is not None else 1
        if current_sv != expected_sv:
            return False, None
        last_step = int(row["totp_last_step"]) if row["totp_last_step"] is not None else None
        if consumed_step is not None and last_step is not None and consumed_step <= last_step:
            return False, None
        current_secret = decrypt_auth_secret(row["totp_secret"]) if row["totp_secret"] else None
        if not current_secret or current_secret != expected_secret:
            return False, None
        new_sv = current_sv + 1
        cursor = conn.execute(
            "UPDATE panel_auth SET totp_enabled = 0, totp_secret = NULL, totp_last_step = NULL, "
            "updated_at = ?, session_version = ? "
            "WHERE username = ? AND totp_enabled = 1 AND session_version = ?",
            (_now(), new_sv, username, current_sv),
        )
        conn.commit()
        if cursor.rowcount > 0:
            return True, new_sv
        return False, None


def record_used_totp_step(username: str, step: int, expected_sv: int | None = None) -> bool:
    """Atomically record used TOTP step to prevent replay.
    Ties the step recording to the expected session_version so delayed requests cannot contaminate.
    """
    with connect() as conn:
        query = (
            "UPDATE panel_auth SET totp_last_step = ? "
            "WHERE username = ? AND (totp_last_step IS NULL OR totp_last_step < ?)"
        )
        params: list[Any] = [step, username, step]
        if expected_sv is not None:
            query += " AND session_version = ?"
            params.append(expected_sv)
        cursor = conn.execute(query, params)
        conn.commit()
        return cursor.rowcount > 0

