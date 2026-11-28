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

            CREATE TABLE IF NOT EXISTS config_peers (
                id TEXT PRIMARY KEY,
                config_id TEXT NOT NULL REFERENCES configs(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                public_key TEXT NOT NULL,
                preshared_key TEXT,
                client_ip TEXT NOT NULL,
                client_config TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );

            CREATE TABLE IF NOT EXISTS realms_brokers (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                listen_port INTEGER NOT NULL DEFAULT 8443,
                token TEXT NOT NULL,
                domain_or_ip TEXT,
                status TEXT NOT NULL DEFAULT 'inactive',
                container_name TEXT,
                instance_path TEXT,
                runtime TEXT,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS realms_tunnels (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                realm_name TEXT NOT NULL,
                broker_type TEXT NOT NULL DEFAULT 'public',
                broker_url TEXT NOT NULL,
                auth_password TEXT NOT NULL,
                pin_sha256 TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'inactive',
                container_name TEXT,
                instance_path TEXT,
                runtime TEXT,
                client_config TEXT,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS standalone_proxies (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                proxy_type TEXT NOT NULL,
                listen_port INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'inactive',
                container_name TEXT,
                instance_path TEXT,
                runtime TEXT,
                client_uri TEXT,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(configs)")}
        for name, kind in (("deploy_owner", "TEXT"), ("lease_expires_at", "REAL")):
            if name not in columns:
                conn.execute(f"ALTER TABLE configs ADD COLUMN {name} {kind}")
        srv_cols = {r["name"] for r in conn.execute("PRAGMA table_info(servers)")}
        if "host_fingerprint" not in srv_cols:
            conn.execute("ALTER TABLE servers ADD COLUMN host_fingerprint TEXT")
        auth_cols = {r["name"] for r in conn.execute("PRAGMA table_info(panel_auth)")}
        if "session_version" not in auth_cols:
            conn.execute("ALTER TABLE panel_auth ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1")
        if "totp_secret" not in auth_cols:
            conn.execute("ALTER TABLE panel_auth ADD COLUMN totp_secret TEXT")
        if "totp_enabled" not in auth_cols:
            conn.execute("ALTER TABLE panel_auth ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")
        if "totp_last_step" not in auth_cols:
            conn.execute("ALTER TABLE panel_auth ADD COLUMN totp_last_step INTEGER")
        broker_cols = {r["name"] for r in conn.execute("PRAGMA table_info(realms_brokers)")}
        if "runtime" not in broker_cols:
            conn.execute("ALTER TABLE realms_brokers ADD COLUMN runtime TEXT")
        tunnel_cols = {r["name"] for r in conn.execute("PRAGMA table_info(realms_tunnels)")}
        if "runtime" not in tunnel_cols:
            conn.execute("ALTER TABLE realms_tunnels ADD COLUMN runtime TEXT")
        for table in ("realms_brokers", "realms_tunnels"):
            rows = conn.execute(f"SELECT id, meta_json FROM {table} WHERE runtime IS NULL AND meta_json IS NOT NULL").fetchall()
            for r in rows:
                try:
                    meta = json.loads(r["meta_json"] or "{}")
                    rt = meta.get("runtime")
                    if rt and isinstance(rt, str) and rt.strip():
                        conn.execute(f"UPDATE {table} SET runtime = ? WHERE id = ?", (rt.strip(), r["id"]))
                except Exception:
                    pass

    migrate_auth_secrets()
    from hoplyra.panel_auth import ensure_default_admin

    ensure_default_admin()


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
    if result.get("protocol") == "awg" and not result.get("awgVersion"):
        result["awgVersion"] = "awg3.1"
    if result.get("protocol") == "xray" and not result.get("vlessUri") and result.get("clientConfig"):
        match = re.search(r"vless://[^\s]+", result["clientConfig"])
        if match:
            result["vlessUri"] = match.group(0)
    return result


def row_to_realm_broker(row: sqlite3.Row) -> dict[str, Any]:
    meta = json.loads(row["meta_json"] or "{}")
    runtime = row["runtime"] if "runtime" in row.keys() and row["runtime"] else meta.get("runtime")
    return {
        "id": row["id"],
        "serverId": row["server_id"],
        "listenPort": row["listen_port"],
        "token": row["token"],
        "domainOrIp": row["domain_or_ip"],
        "status": row["status"],
        "containerName": row["container_name"],
        "instancePath": row["instance_path"],
        "runtime": runtime,
        "createdAt": row["created_at"],
        "meta": meta,
    }


def row_to_realm_tunnel(row: sqlite3.Row) -> dict[str, Any]:
    meta = json.loads(row["meta_json"] or "{}")
    runtime = row["runtime"] if "runtime" in row.keys() and row["runtime"] else meta.get("runtime")
    return {
        "id": row["id"],
        "serverId": row["server_id"],
        "name": row["name"],
        "realmName": row["realm_name"],
        "brokerType": row["broker_type"],
        "brokerUrl": row["broker_url"],
        "authPassword": row["auth_password"],
        "pinSha256": row["pin_sha256"],
        "status": row["status"],
        "containerName": row["container_name"],
        "instancePath": row["instance_path"],
        "clientConfig": row["client_config"],
        "runtime": runtime,
        "createdAt": row["created_at"],
        "meta": meta,
    }


def row_to_standalone_proxy(row: sqlite3.Row) -> dict[str, Any]:
    meta = json.loads(row["meta_json"] or "{}")
    runtime = row["runtime"] if "runtime" in row.keys() and row["runtime"] else meta.get("runtime")
    return {
        "id": row["id"],
        "serverId": row["server_id"],
        "name": row["name"],
        "proxyType": row["proxy_type"],
        "listenPort": row["listen_port"],
        "status": row["status"],
        "containerName": row["container_name"],
        "instancePath": row["instance_path"],
        "runtime": runtime,
        "clientUri": row["client_uri"],
        "createdAt": row["created_at"],
        "meta": meta,
    }


def new_id() -> str:
    return str(uuid.uuid4())
