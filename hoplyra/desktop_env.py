from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_desktop_env(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    env_path = data_dir / ".env"
    if env_path.is_file():
        load_env_file(env_path)
        return

    session_secret = secrets.token_hex(32)
    fernet_key = Fernet.generate_key().decode()
    env_path.write_text(
        "\n".join(
            [
                f"HOPLYRA_SESSION_SECRET={session_secret}",
                f"HOPLYRA_SECRET_KEY={fernet_key}",
                "HOPLYRA_ADMIN_USER=admin",
                "HOPLYRA_ADMIN_PASSWORD=admin",
                "",
            ]
        ),
        encoding="utf-8",
    )
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    load_env_file(env_path)


def bootstrap_from_env() -> None:
    desktop = os.environ.get("HOPLYRA_DESKTOP", "").strip().lower() in {"1", "true", "yes"}
    if not desktop:
        return
    raw_data = os.environ.get("HOPLYRA_DATA", "").strip()
    if not raw_data:
        return
    ensure_desktop_env(Path(raw_data))
