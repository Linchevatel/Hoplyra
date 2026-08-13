from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hoplyra.chains.planner import normalize_stored_hops
from hoplyra.secrets import encrypt_auth_secret, is_encrypted
from hoplyra.socks_proxy import socks_proxy_for_response

DATA_DIR = Path(os.environ.get("HOPLYRA_DATA", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "hoplyra.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                username TEXT NOT NULL DEFAULT 'root',
                auth_type TEXT NOT NULL DEFAULT 'none',
                auth_secret TEXT,
                os TEXT,
                location TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'connecting',
                latency_ms INTEGER,
                last_seen TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS configs (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                protocol TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'inactive',
                client_config TEXT,
                container_name TEXT,
                instance_path TEXT,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS panel_auth (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _migrate_configs_unique_constraint(conn)
    migrate_auth_secrets()
    deduplicate_servers()
    from hoplyra.panel_auth import ensure_default_admin

    ensure_default_admin()


def _migrate_configs_unique_constraint(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='configs'").fetchone()
    if row and row["sql"] and "UNIQUE(server_id, protocol)" in row["sql"]:
        conn.execute("ALTER TABLE configs RENAME TO configs_old")
        conn.execute(
            """
            CREATE TABLE configs (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                protocol TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'inactive',
                client_config TEXT,
                container_name TEXT,
                instance_path TEXT,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO configs (id, server_id, protocol, status, client_config, container_name, instance_path, meta_json, created_at)
            SELECT id, server_id, protocol, status, client_config, container_name, instance_path, meta_json, created_at
            FROM configs_old
            """
        )
        conn.execute("DROP TABLE configs_old")


def deduplicate_servers() -> None:
    with connect() as conn:
        dups = conn.execute(
            """
            SELECT host, port, COUNT(*) as cnt
            FROM servers
            WHERE host NOT IN ('127.0.0.1', 'localhost', '::1')
            GROUP BY host, port
            HAVING cnt > 1
            """
        ).fetchall()
        for d in dups:
            host, port = d["host"], d["port"]
            rows = conn.execute(
                "SELECT id FROM servers WHERE host = ? AND port = ? ORDER BY created_at DESC",
                (host, port),
            ).fetchall()
            if len(rows) <= 1:
                continue
            keep_id = rows[0]["id"]
            dup_ids = [r["id"] for r in rows[1:]]
            for dup_id in dup_ids:
                conn.execute(
                    "UPDATE OR IGNORE configs SET server_id = ? WHERE server_id = ?",
                    (keep_id, dup_id),
                )
                conn.execute("DELETE FROM servers WHERE id = ?", (dup_id,))



def migrate_auth_secrets() -> None:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, auth_secret FROM servers WHERE auth_secret IS NOT NULL AND auth_secret != ''",
        ).fetchall()
        for row in rows:
            secret = row["auth_secret"]
            if is_encrypted(secret):
                continue
            conn.execute(
                "UPDATE servers SET auth_secret=? WHERE id=?",
                (encrypt_auth_secret(secret), row["id"]),
            )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_server(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "username": row["username"],
        "status": row["status"],
        "os": row["os"],
        "location": row["location"],
        "tags": json.loads(row["tags"] or "[]"),
        "notes": row["notes"],
        "latencyMs": row["latency_ms"] if row["latency_ms"] else None,
        "lastSeen": row["last_seen"],
        "activeProtocol": None,
    }


def row_to_config(row: sqlite3.Row) -> dict[str, Any]:
    meta = json.loads(row["meta_json"] or "{}")
    result = {
        "id": row["id"],
        "serverId": row["server_id"],
        "protocol": row["protocol"],
        "status": row["status"],
        "clientConfig": row["client_config"],
        "createdAt": row["created_at"],
    }
    if meta.get("hops"):
        result["hops"] = normalize_stored_hops(meta["hops"])
    if meta.get("hopDeployStatus"):
        result["hopDeployStatus"] = meta["hopDeployStatus"]
    if meta.get("statusMessage"):
        result["statusMessage"] = meta["statusMessage"]
    extra = {
        k: v
        for k, v in meta.items()
        if k not in ("hops", "hopDeployStatus", "chain", "socksProxy")
    }
    result.update(extra)
    proxy = socks_proxy_for_response(meta)
    if proxy:
        result["socksProxy"] = proxy
    if result.get("protocol") == "awg":
        awg_params = meta.get("awgParams") or {}
        awg_ver = meta.get("awgVersion")
        if not awg_ver and isinstance(awg_params, dict):
            awg_ver = awg_params.get("awgVersion")
        if awg_ver:
            result["awgVersion"] = awg_ver
        if result.get("clientConfig"):
            from hoplyra.amnezia_export import build_amnezia_awg_vpn_uri
            match = re.search(r"Endpoint\s*=\s*([^\s:]+):(\d+)", result["clientConfig"])
            if match:
                srv_host = match.group(1)
                srv_port = int(match.group(2))
                result["amneziaVpnUri"] = build_amnezia_awg_vpn_uri(
                    result["clientConfig"],
                    host=srv_host,
                    port=srv_port,
                    description=f"Hoplyra {result['id'][:8]}",
                )
    if result.get("protocol") == "xray" and not result.get("vlessUri") and result.get("clientConfig"):
        match = re.search(r"vless://[^\s]+", result["clientConfig"])
        if match:
            result["vlessUri"] = match.group(0)
    return result


def new_id() -> str:
    return str(uuid.uuid4())
