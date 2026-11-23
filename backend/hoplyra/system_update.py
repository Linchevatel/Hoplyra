"""System update checker and updater for Hoplyra.

Hardened release updater:
1. Deadlock-free concurrency: _UPDATE_LOCK is not held across network requests or nested calls.
2. Server-side release verification: Target tag must match confirmed releases/latest.
3. Single lockfile owner: Subprocess owns flock on /tmp/hoplyra_update.lock.
4. Worktree staging and fail-safe rollback: Verifies before checkout; rolls back on failed health check.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("hoplyra.system_update")

_CURRENT_VERSION = "v1.7.0"
_GITHUB_REPO = "Linchevatel/Hoplyra"
_CACHE_TTL_SECONDS = 900  # 15 minutes cache for GitHub releases

_UPDATE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}
_LAST_CHECK_TIME = 0.0

_LOCKFILE_PATH = Path("/tmp/hoplyra_update.lock")
_TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$")


def get_repo_root() -> Path:
    """Find actual git repository root."""
    p = Path(__file__).resolve()
    for parent in [p.parent, p.parents[1], p.parents[2]]:
        if (parent / ".git").exists():
            return parent
    prod = Path("/root/Hoplyra")
    if (prod / ".git").exists():
        return prod
    return p.parents[2]


def get_current_version() -> str:
    """Read current version from git tag or fallback to constant."""
    repo_root = get_repo_root()
    try:
        res = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=str(repo_root),
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass

    try:
        res = subprocess.run(
            ["git", "describe", "--tags"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=str(repo_root),
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().split("-")[0]
    except Exception:
        pass

    return _CURRENT_VERSION


def _parse_version(v: str) -> tuple[int, ...]:
    clean = re.sub(r"^[^\d]*", "", v.strip())
    clean = clean.split("-")[0]
    parts: list[int] = []
    for item in clean.split("."):
        try:
            parts.append(int(item))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_for_updates(force: bool = False) -> dict[str, Any]:
    """Check GitHub releases for updates without holding lock during HTTP."""
    global _LAST_CHECK_TIME, _CACHE

    now = time.time()
    current_ver = get_current_version()

    with _UPDATE_LOCK:
        if not force and _CACHE and (now - _LAST_CHECK_TIME < _CACHE_TTL_SECONDS):
            cached = dict(_CACHE)
            cached["currentVersion"] = current_ver
            cached["hasUpdate"] = _parse_version(cached.get("latestVersion", "")) > _parse_version(current_ver)
            return cached

    url = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Hoplyra-Updater",
            "Accept": "application/vnd.github.v3+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.warning("Failed to fetch latest release from GitHub: %s", exc)
        with _UPDATE_LOCK:
            if _CACHE:
                cached = dict(_CACHE)
                cached["currentVersion"] = current_ver
                cached["hasUpdate"] = _parse_version(cached.get("latestVersion", "")) > _parse_version(current_ver)
                return cached
        return {
            "currentVersion": current_ver,
            "latestVersion": current_ver,
            "hasUpdate": False,
            "releaseNotes": "",
            "publishedAt": "",
            "releaseUrl": "",
            "error": str(exc),
        }

    latest_tag = data.get("tag_name", "").strip()
    release_notes = data.get("body", "").strip()
    published_at = data.get("published_at", "").strip()
    release_url = data.get("html_url", "").strip()

    has_update = _parse_version(latest_tag) > _parse_version(current_ver)

    result = {
        "currentVersion": current_ver,
        "latestVersion": latest_tag or current_ver,
        "hasUpdate": has_update,
        "releaseNotes": release_notes,
        "publishedAt": published_at,
        "releaseUrl": release_url,
    }

    with _UPDATE_LOCK:
        _CACHE = dict(result)
        _LAST_CHECK_TIME = now

    return result


def is_update_in_progress() -> bool:
    """Check if update is currently active via lockfile or systemd."""
    if _LOCKFILE_PATH.exists():
        try:
            with open(_LOCKFILE_PATH, "r") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
        except (BlockingIOError, OSError):
            return True

    try:
        res = subprocess.run(
            ["systemctl", "is-active", "hoplyra-updater.service"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.stdout.strip() in ("active", "activating"):
            return True
    except Exception:
        pass

    return False


def apply_update_async(target_version: str | None = None) -> dict[str, Any]:
    """Validate release outside lock, then launch update process under atomic reservation."""
    # 1. Fetch/validate release OUTSIDE of threading lock to avoid deadlocks and blocking HTTP
    info = check_for_updates(force=False)
    latest_tag = info.get("latestVersion", "").strip()
    requested_tag = (target_version or latest_tag).strip()

    if not requested_tag or not _TAG_PATTERN.match(requested_tag):
        return {
            "ok": False,
            "status": "error",
            "message": f"Недопустимый формат версии релиза: '{requested_tag}'.",
        }

    if requested_tag != latest_tag:
        return {
            "ok": False,
            "status": "error",
            "message": f"Запрошенная версия '{requested_tag}' не совпадает с подтверждённым релизом '{latest_tag}'.",
        }

    repo_root = get_repo_root()
    script_path = repo_root / "scripts" / "self_update.sh"
    if not script_path.exists():
        return {
            "ok": False,
            "status": "error",
            "message": f"Скрипт обновления {script_path} не найден.",
        }

    python_bin = sys.executable
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists() and os.access(venv_python, os.X_OK):
        python_bin = str(venv_python)

    health_port = os.environ.get("HOPLYRA_PORT", "8787")
    ssl_cert = os.environ.get("HOPLYRA_SSL_CERTFILE") or os.environ.get("SSL_CERTFILE")
    ssl_key = os.environ.get("HOPLYRA_SSL_KEYFILE") or os.environ.get("SSL_KEYFILE")
    health_scheme = "https" if (ssl_cert and ssl_key) else "http"

    # 2. Critical section: verify no update in progress and spawn exactly one child
    with _UPDATE_LOCK:
        if is_update_in_progress():
            return {
                "ok": False,
                "status": "busy",
                "message": "Обновление уже выполняется. Пожалуйста, подождите.",
            }

        args = [str(script_path), str(repo_root), requested_tag, python_bin, str(health_port), health_scheme]
        launched = False

        try:
            subprocess.run(["systemctl", "reset-failed", "hoplyra-updater.service"], capture_output=True, timeout=2)
            cmd = ["systemd-run", "--unit=hoplyra-updater", "--remain-after-exit=no", "--"] + args
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                launched = True
            else:
                log.warning("systemd-run failed with code %s: %s", res.returncode, res.stderr)
        except Exception as exc:
            log.warning("systemd-run launch failed: %s", exc)

        if not launched:
            # Re-check in case systemd-run failed due to already running
            if is_update_in_progress():
                return {
                    "ok": False,
                    "status": "busy",
                    "message": "Обновление уже выполняется.",
                }
            try:
                subprocess.Popen(
                    ["setsid"] + args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                launched = True
            except Exception as exc:
                log.error("Failed to launch detached update process: %s", exc)
                return {
                    "ok": False,
                    "status": "error",
                    "message": f"Не удалось запустить процесс обновления: {exc}",
                }

    return {
        "ok": True,
        "status": "updating",
        "targetVersion": requested_tag,
        "message": f"Запущено обновление до {requested_tag}. Панель перезапустится автоматически.",
    }
