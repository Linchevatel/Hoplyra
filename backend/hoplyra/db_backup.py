from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hoplyra.db import DATA_DIR, DB_PATH

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_NAME_RE = re.compile(r"^hoplyra-[0-9A-Za-z._-]+\.db$")
REQUIRED_TABLES = frozenset({"servers", "configs", "panel_auth"})


def backup_dir_path() -> str:
    return str(BACKUP_DIR.resolve())


def _validate_hoplyra_db(path: Path) -> None:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ValueError(f"Not a Hoplyra database (missing tables: {', '.join(sorted(missing))})")
    finally:
        conn.close()


def resolve_backup_name(name: str) -> Path:
    clean = name.strip()
    if not BACKUP_NAME_RE.fullmatch(clean):
        raise ValueError("Invalid backup file name")
    path = (BACKUP_DIR / clean).resolve()
    backup_root = BACKUP_DIR.resolve()
    if path.parent != backup_root:
        raise ValueError("Invalid backup path")
    if not path.is_file():
        raise FileNotFoundError(clean)
    return path


def create_db_backup(*, backup_dir: Path | None = None, label: str = "") -> Path:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    target_dir = backup_dir or BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    dest = target_dir / f"hoplyra{suffix}-{stamp}.db"

    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    return dest


def list_db_backups() -> list[dict[str, Any]]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in BACKUP_DIR.glob("hoplyra-*.db"):
        if not path.is_file():
            continue
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "path": str(path.resolve()),
                "sizeBytes": stat.st_size,
                "createdAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    items.sort(key=lambda item: item["createdAt"], reverse=True)
    return items


def _checkpoint_wal() -> None:
    if not DB_PATH.is_file():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def _remove_wal_sidecars() -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = DB_PATH.with_name(DB_PATH.name + suffix)
        sidecar.unlink(missing_ok=True)


def restore_db_backup(source: Path) -> tuple[Path, Path]:
    source = source.resolve()
    _validate_hoplyra_db(source)

    pre_restore = create_db_backup(label="pre-restore") if DB_PATH.is_file() else None
    _checkpoint_wal()

    tmp = DB_PATH.with_suffix(".db.restore-tmp")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    _validate_hoplyra_db(tmp)
    tmp.replace(DB_PATH)
    _remove_wal_sidecars()
    return pre_restore, source


def restore_db_backup_by_name(name: str) -> tuple[Path, Path]:
    return restore_db_backup(resolve_backup_name(name))


def save_uploaded_backup(content: bytes, *, original_name: str = "upload.db") -> Path:
    if len(content) < 512:
        raise ValueError("Backup file is too small")
    if len(content) > 100 * 1024 * 1024:
        raise ValueError("Backup file is too large (max 100 MB)")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_tail = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original_name).stem)[:40] or "upload"
    dest = BACKUP_DIR / f"hoplyra-upload-{safe_tail}-{stamp}.db"
    dest.write_bytes(content)
    _validate_hoplyra_db(dest)
    return dest
