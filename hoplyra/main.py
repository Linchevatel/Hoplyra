from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from hoplyra import db
from hoplyra.auth import (
    SessionAuthMiddleware,
    session_secret_key,
    warn_if_insecure,
)
from hoplyra.panel_auth import (
    admin_username,
    change_admin_password,
    is_default_password,
    verify_admin_credentials,
)
from hoplyra.config_lifecycle import restart_remote_config, stop_remote_config
from hoplyra.deploy_jobs import (
    is_deploy_active,
    recover_stuck_deploying,
    schedule_recover_stuck_deploying,
    start_chain_deploy_job,
    start_single_deploy_job,
    wait_for_deploy_jobs,
)
from hoplyra.db import DATA_DIR, connect, init_db, new_id, row_to_config, row_to_server
from hoplyra.db_backup import (
    backup_dir_path,
    create_db_backup,
    list_db_backups,
    restore_db_backup,
    restore_db_backup_by_name,
    save_uploaded_backup,
)
from hoplyra.hosts import validate_host
from hoplyra.secrets import decrypt_auth_secret, encrypt_auth_secret
from hoplyra.chains.deployer import _chain_tag
from hoplyra.chains.planner import build_plan, validate_chain
from hoplyra.chains.route_test import test_chain_route
from hoplyra.chains.preflight import ChainPreflightError, recheck_runners_online, verify_chain_servers
from hoplyra.protocols import DEPLOYERS, get_deployer_for_config
from hoplyra.remote import RemoteRunner, ServerTarget, is_local_host, probe_server, test_ssh_auth
from hoplyra.host_setup import ensure_host_ready
from hoplyra.server_prepare import get_prepare_job, start_prepare_job
from hoplyra.server_metrics import CONTROL_SERVER_ID, collect_local_metrics, metrics_for_target
from hoplyra.metrics_cache import metrics_cache
from hoplyra.geo import ensure_location, lookup_location
from hoplyra.awg_runtime import (
    awg_server_ids,
    chain_tag_from_config_id,
    config_uses_awg,
    repair_awg_for_amnezia,
    upgrade_awg_on_host,
)

ssl_enabled = bool(
    (os.environ.get("HOPLYRA_SSL_CERTFILE") and os.environ.get("HOPLYRA_SSL_KEYFILE"))
    or (os.environ.get("SSL_CERTFILE") and os.environ.get("SSL_KEYFILE"))
    or os.environ.get("HOPLYRA_HTTPS_ONLY", "").strip().lower() in ("1", "true", "yes")
)

app = FastAPI(title="Hoplyra API", version="1.3.2")
log = logging.getLogger("hoplyra")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionAuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret_key(),
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
    https_only=ssl_enabled,
)


SINGLE_PROTOCOLS = frozenset(DEPLOYERS.keys())


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=4, max_length=256)


class RestoreBackupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ServerCreate(BaseModel):
    name: str
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str = "root"
    authSecret: str = Field(min_length=1)
    location: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class ServerUpdate(BaseModel):
    name: str | None = None
    tags: list[str] | None = None
    notes: str | None = None


class DeployRequest(BaseModel):
    serverId: str
    protocol: str
    transport: str | None = None
    xrayBypass: bool = False


def _normalize_auth(body: ServerCreate) -> ServerTarget:
    secret = body.authSecret.strip()
    upper = secret.upper()
    if "BEGIN" in upper and "PRIVATE KEY" in upper:
        raise HTTPException(400, "Нужен пароль SSH (root), не содержимое ключа.")
    if secret.startswith("~/.ssh/") or (secret.startswith("/") and "id_" in secret):
        raise HTTPException(400, "Нужен пароль SSH пользователя, не путь к ключу.")
    try:
        host = validate_host(body.host)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ServerTarget(
        host=host,
        port=body.port,
        username=body.username.strip(),
        auth_type="password",
        auth_secret=secret,
    )


def _get_server_row(server_id: str) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Server not found")
    return row


def _server_target(row: Any) -> ServerTarget:
    secret = decrypt_auth_secret(row["auth_secret"])
    if secret is None:
        raise HTTPException(500, "Server credentials missing")
    return ServerTarget(
        host=row["host"],
        port=row["port"],
        username=row["username"],
        auth_type="password",
        auth_secret=secret,
    )


def _enrich_server(server: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn:
        cfg = conn.execute(
            "SELECT protocol FROM configs WHERE server_id = ? AND status = 'active' LIMIT 1",
            (server["id"],),
        ).fetchone()
    if cfg:
        server["activeProtocol"] = cfg["protocol"]
    job = get_prepare_job(server["id"])
    if job:
        server["prepareProgress"] = job.to_dict()
    return server


def _server_response(row: Any) -> dict[str, Any]:
    return _enrich_server(ensure_location(row_to_server(row)))


def _backfill_locations() -> None:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM servers WHERE location IS NULL OR location = ''",
        ).fetchall()
    for row in rows:
        if is_local_host(row["host"]):
            continue
        ensure_location(row_to_server(row))


def _local_server_ids(conn: Any) -> set[str]:
    rows = conn.execute("SELECT id, host FROM servers").fetchall()
    return {r["id"] for r in rows if is_local_host(r["host"])}


def _config_uses_local(row: Any, local_ids: set[str]) -> bool:
    if row["server_id"] in local_ids:
        return True
    meta = json.loads(row["meta_json"] or "{}")
    for hop in meta.get("hops", []):
        if hop.get("serverId") in local_ids:
            return True
    return False


def _remote_server_rows(conn: Any) -> list[Any]:
    rows = conn.execute("SELECT * FROM servers ORDER BY created_at DESC").fetchall()
    return [r for r in rows if not is_local_host(r["host"])]


def _load_remote_server_rows() -> list[Any]:
    with connect() as conn:
        return _remote_server_rows(conn)


def _configs_referencing_server(conn: Any, server_id: str) -> list[Any]:
    rows = list(conn.execute("SELECT * FROM configs WHERE server_id = ?", (server_id,)).fetchall())
    seen = {r["id"] for r in rows}
    for cfg in conn.execute("SELECT * FROM configs").fetchall():
        if cfg["id"] in seen:
            continue
        meta = json.loads(cfg["meta_json"] or "{}")
        for hop in meta.get("hops") or []:
            if hop.get("serverId") == server_id:
                rows.append(cfg)
                seen.add(cfg["id"])
                break
    return rows


def _config_meta(cfg: Any) -> dict[str, Any]:
    return json.loads(cfg["meta_json"] or "{}")


def _is_active_chain_config(cfg: Any) -> bool:
    return bool(_config_meta(cfg).get("chain")) and cfg["status"] == "active"


def _assert_active_chain_immutable(cfg: Any) -> None:
    if _is_active_chain_config(cfg):
        raise HTTPException(
            409,
            "Активную цепь нельзя изменять — только удалить. Остановка, перезапуск и repair недоступны.",
        )


def _server_has_active_config(conn: Any, server_id: str) -> bool:
    for cfg in _configs_referencing_server(conn, server_id):
        if cfg["status"] == "active":
            return True
        if cfg["status"] == "deploying" and is_deploy_active(cfg["id"]):
            return True
    return False


def _assert_servers_available_for_deploy(conn: Any, server_ids: list[str]) -> None:
    for sid in server_ids:
        if _server_has_active_config(conn, sid):
            row = conn.execute("SELECT name FROM servers WHERE id = ?", (sid,)).fetchone()
            label = row["name"] if row else sid
            raise HTTPException(409, f"Сервер «{label}» занят активной цепью или VPN — выберите другой VPS")


def _purge_configs_on_server(configs: list[Any]) -> None:
    for cfg in configs:
        try:
            stop_remote_config(
                dict(cfg),
                get_server_row=_get_server_row,
                server_target=_server_target,
                purge=True,
            )
        except Exception as exc:
            log.warning("Cleanup config %s on server delete failed: %s", cfg["id"], exc)


def _apply_server_probe(row: Any, probe: dict[str, Any], *, runtime: str | None = None) -> None:
    status = probe["status"]
    if runtime:
        status = "online"
    with connect() as conn:
        conn.execute(
            "UPDATE servers SET status=?, latency_ms=?, last_seen=?, os=COALESCE(?, os) WHERE id=?",
            (
                status,
                probe.get("latencyMs"),
                probe.get("lastSeen") or db._now(),
                probe.get("os"),
                row["id"],
            ),
        )


def _refresh_server_row(row: Any) -> None:
    target = _server_target(row)
    ssh_ok, _ssh_err = test_ssh_auth(target)
    if not ssh_ok:
        with connect() as conn:
            conn.execute(
                "UPDATE servers SET status='offline', last_seen=? WHERE id=?",
                (db._now(), row["id"]),
            )
        return

    probe = probe_server(target)
    runtime: str | None = None
    try:
        setup = ensure_host_ready(RemoteRunner(target))
        runtime = setup.get("runtime")
    except Exception:
        pass
    if runtime:
        probe["status"] = "online"
        probe["podmanVersion"] = runtime
    _apply_server_probe(row, probe, runtime=runtime)


def _recheck_offline_servers_loop() -> None:
    time.sleep(8)
    while True:
        try:
            with connect() as conn:
                rows = [
                    r
                    for r in _remote_server_rows(conn)
                    if r["status"] in ("offline", "error")
                ]
            for row in rows:
                try:
                    _refresh_server_row(row)
                except Exception:
                    log.debug("offline recheck failed for %s", row["host"], exc_info=True)
                time.sleep(1.5)
        except Exception:
            log.debug("offline recheck loop failed", exc_info=True)
        time.sleep(30)


@app.on_event("startup")
def startup() -> None:
    init_db()
    schedule_recover_stuck_deploying()
    warn_if_insecure()
    metrics_cache.start(_load_remote_server_rows)
    threading.Thread(target=metrics_cache.refresh, daemon=True, name="hoplyra-metrics-initial").start()
    threading.Thread(target=_backfill_locations, daemon=True, name="hoplyra-geo-backfill").start()
    threading.Thread(
        target=_recheck_offline_servers_loop,
        daemon=True,
        name="hoplyra-offline-recheck",
    ).start()


@app.on_event("shutdown")
def shutdown() -> None:
    wait_for_deploy_jobs(timeout=120)
    metrics_cache.stop()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    if request.session.get("authenticated"):
        return {
            "authenticated": True,
            "username": request.session.get("user") or admin_username(),
            "authRequired": True,
            "defaultPassword": is_default_password(),
        }
    return {"authenticated": False, "authRequired": True, "defaultPassword": is_default_password()}


@app.post("/api/auth/login")
def auth_login(body: LoginRequest, request: Request) -> dict[str, Any]:
    if not verify_admin_credentials(body.username, body.password):
        raise HTTPException(401, "invalid_credentials")
    request.session["authenticated"] = True
    request.session["user"] = body.username.strip()
    return {
        "ok": True,
        "username": request.session["user"],
        "defaultPassword": is_default_password(),
    }


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@app.post("/api/auth/change-password")
def auth_change_password(body: ChangePasswordRequest, request: Request) -> dict[str, bool]:
    if not request.session.get("authenticated"):
        raise HTTPException(401, "Unauthorized")
    try:
        change_admin_password(body.currentPassword, body.newPassword)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/settings/db-backup-info")
def settings_db_backup_info() -> dict[str, Any]:
    return {"backupDir": backup_dir_path(), "backups": list_db_backups()}


@app.post("/api/settings/db-backup")
def settings_db_backup() -> FileResponse:
    try:
        backup_path = create_db_backup()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}") from exc
    return FileResponse(
        backup_path,
        media_type="application/x-sqlite3",
        filename=backup_path.name,
        headers={"X-Hoplyra-Backup-Path": str(backup_path.resolve())},
    )


@app.post("/api/settings/db-backup/restore")
def settings_db_restore(body: RestoreBackupRequest) -> dict[str, str]:
    try:
        pre_restore, restored = restore_db_backup_by_name(body.name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc
    try:
        metrics_cache.refresh()
    except Exception:
        log.exception("metrics refresh after db restore failed")
    return {
        "ok": True,
        "restoredFrom": str(restored.resolve()),
        "preRestoreBackup": str(pre_restore.resolve()) if pre_restore else "",
    }


@app.post("/api/settings/db-backup/restore-upload")
async def settings_db_restore_upload(file: UploadFile = File(...)) -> dict[str, str]:
    if not file.filename or not file.filename.lower().endswith(".db"):
        raise HTTPException(status_code=400, detail="Expected a .db SQLite backup file")
    content = await file.read()
    try:
        uploaded = save_uploaded_backup(content, original_name=file.filename)
        pre_restore, restored = restore_db_backup(uploaded)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc
    try:
        metrics_cache.refresh()
    except Exception:
        log.exception("metrics refresh after db restore failed")
    return {
        "ok": True,
        "restoredFrom": str(restored.resolve()),
        "preRestoreBackup": str(pre_restore.resolve()) if pre_restore else "",
    }


@app.get("/api/servers")
def list_servers() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM servers ORDER BY created_at DESC").fetchall()
    return [_server_response(r) for r in rows if not is_local_host(r["host"])]


@app.get("/api/servers/metrics")
def list_servers_metrics() -> list[dict[str, Any]]:
    snap = metrics_cache.get_snapshot()
    if snap["metrics"]:
        return snap["metrics"]
    return [collect_local_metrics()]


@app.get("/api/servers/metrics/stream")
async def stream_servers_metrics() -> StreamingResponse:
    async def event_gen():
        last_ts: str | None = None
        while True:
            snap = metrics_cache.get_snapshot()
            ts = snap.get("ts")
            metrics = snap.get("metrics") or []
            if metrics and ts != last_ts:
                envelope = {"metrics": metrics, "ts": ts}
                yield f"data: {json.dumps(envelope)}\n\n"
                last_ts = ts
            await asyncio.sleep(1)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/servers/{server_id}/metrics")
def get_server_metrics(server_id: str) -> dict[str, Any]:
    row = _get_server_row(server_id)
    if is_local_host(row["host"]):
        raise HTTPException(404, "Server not found")
    runner = RemoteRunner(_server_target(row))
    return metrics_for_target(
        row["id"],
        row["name"],
        row["host"],
        runner,
        latency_ms=row["latency_ms"],
        status=row["status"],
    )


@app.post("/api/servers")
def create_server(body: ServerCreate) -> dict[str, Any]:
    target = _normalize_auth(body)
    if is_local_host(target.host):
        raise HTTPException(
            status_code=400,
            detail="Local address (127.0.0.1, localhost) cannot be added as a VPS.",
        )

    ssh_ok, ssh_err = test_ssh_auth(target)
    if not ssh_ok:
        raise HTTPException(
            status_code=400,
            detail=ssh_err or "SSH connection failed. Check the password and SSH access.",
        )

    log.info("SSH ok for %s@%s — starting background VPS prepare", target.username, target.host)
    server_id = new_id()
    location = body.location or lookup_location(target.host)

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret,
                os, location, tags, notes, status, latency_ms, last_seen, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                server_id,
                body.name.strip(),
                target.host,
                target.port,
                target.username,
                target.auth_type,
                encrypt_auth_secret(target.auth_secret),
                None,
                location,
                json.dumps(body.tags),
                body.notes,
                "connecting",
                None,
                None,
                db._now(),
            ),
        )
        row = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()

    start_prepare_job(
        server_id,
        target,
        name=body.name.strip(),
        location=location,
        tags=body.tags,
        notes=body.notes,
    )

    result = _server_response(row)
    result["statusMessage"] = "Подготовка VPS: установка Podman/Docker"
    return result


@app.get("/api/servers/{server_id}/prepare")
def get_server_prepare_status(server_id: str) -> dict[str, Any]:
    row = _get_server_row(server_id)
    job = get_prepare_job(server_id)
    if job:
        return job.to_dict()
    if row["status"] == "connecting":
        return {
            "serverId": server_id,
            "percent": 5,
            "stage": "ssh",
            "message": "Подготовка VPS…",
            "status": "running",
        }
    if row["status"] in ("online", "offline"):
        return {
            "serverId": server_id,
            "percent": 100,
            "stage": "done",
            "message": "Готово",
            "status": "done",
        }
    if row["status"] == "error":
        return {
            "serverId": server_id,
            "percent": 100,
            "stage": "error",
            "message": "Ошибка подготовки VPS",
            "status": "error",
        }
    raise HTTPException(404, "Prepare job not found")


@app.post("/api/servers/{server_id}/prepare")
def prepare_server(server_id: str) -> dict[str, Any]:
    row = _get_server_row(server_id)
    target = _server_target(row)
    ssh_ok, ssh_err = test_ssh_auth(target)
    if not ssh_ok:
        raise HTTPException(400, ssh_err or "SSH недоступен")
    try:
        setup = ensure_host_ready(RemoteRunner(target), force=True)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    probe = probe_server(target)
    if setup.get("runtime"):
        probe["status"] = "online"
        probe["podmanVersion"] = setup["runtime"]
    with connect() as conn:
        conn.execute(
            "UPDATE servers SET status=?, last_seen=?, os=COALESCE(?, os) WHERE id=?",
            (probe["status"], probe.get("lastSeen"), probe.get("os"), server_id),
        )
        updated = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    result = _server_response(updated)
    result["statusMessage"] = "VPS подготовлен"
    if setup.get("runtime"):
        result["podmanVersion"] = setup["runtime"]
    return result


@app.delete("/api/servers/{server_id}")
def delete_server(server_id: str) -> dict[str, bool]:
    _get_server_row(server_id)
    with connect() as conn:
        configs = _configs_referencing_server(conn, server_id)
        blocking = [c for c in configs if c["status"] in ("active", "deploying")]
        if blocking:
            raise HTTPException(
                409,
                "На сервере есть активные VPN или цепи. Сначала остановите и удалите конфигурации.",
            )
        inactive = [c for c in configs if c["status"] not in ("active", "deploying")]
        config_ids = [c["id"] for c in configs]
    _purge_configs_on_server(inactive)
    with connect() as conn:
        for cid in config_ids:
            conn.execute("DELETE FROM configs WHERE id = ?", (cid,))
        cur = conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Server not found")
    return {"ok": True}


@app.patch("/api/servers/{server_id}")
def update_server(server_id: str, body: ServerUpdate) -> dict[str, Any]:
    row = _get_server_row(server_id)
    if is_local_host(row["host"]):
        raise HTTPException(404, "Server not found")

    fields: list[str] = []
    values: list[Any] = []

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Название сервера не может быть пустым")
        if len(name) > 128:
            raise HTTPException(400, "Название слишком длинное")
        fields.append("name=?")
        values.append(name)

    if body.tags is not None:
        tags = [t.strip().lower() for t in body.tags if t.strip()]
        fields.append("tags=?")
        values.append(json.dumps(tags))

    if body.notes is not None:
        notes = body.notes.strip() if body.notes else ""
        fields.append("notes=?")
        values.append(notes or None)

    if not fields:
        return _server_response(row)

    values.append(server_id)
    with connect() as conn:
        conn.execute(f"UPDATE servers SET {', '.join(fields)} WHERE id=?", values)
        updated = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    return _server_response(updated)


@app.post("/api/servers/{server_id}/ping")
def ping_server(server_id: str) -> dict[str, Any]:
    row = _get_server_row(server_id)
    target = _server_target(row)
    ssh_ok, ssh_err = test_ssh_auth(target)
    if not ssh_ok:
        with connect() as conn:
            conn.execute(
                "UPDATE servers SET status='offline', last_seen=? WHERE id=?",
                (db._now(), server_id),
            )
            updated = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
        result = _server_response(updated)
        result["statusMessage"] = ssh_err
        return result

    probe = probe_server(target)
    runner = RemoteRunner(target)
    try:
        setup = ensure_host_ready(runner)
        if setup.get("runtime"):
            probe["status"] = "online"
            probe["podmanVersion"] = setup["runtime"]
    except Exception as exc:
        result = _server_response(row)
        result["statusMessage"] = str(exc)[:500]
        return result

    _apply_server_probe(row, probe, runtime=probe.get("podmanVersion"))
    with connect() as conn:
        updated = conn.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    result = _server_response(updated)
    if probe.get("message"):
        result["statusMessage"] = probe["message"]
    if probe.get("podmanVersion"):
        result["podmanVersion"] = probe["podmanVersion"]
    return result


@app.get("/api/configs")
def list_configs() -> list[dict[str, Any]]:
    with connect() as conn:
        local_ids = _local_server_ids(conn)
        rows = conn.execute("SELECT * FROM configs ORDER BY created_at DESC").fetchall()
    return [row_to_config(r) for r in rows if not _config_uses_local(r, local_ids)]


class ChainHopRequest(BaseModel):
    id: str | None = None
    protocol: str
    serverId: str
    transport: str | None = None
    xrayBypass: bool = False


class DeployChainRequest(BaseModel):
    hops: list[ChainHopRequest]


def _hop_payload(hops: list[ChainHopRequest]) -> list[dict[str, Any]]:
    import uuid as _uuid

    out: list[dict[str, Any]] = []
    for h in hops:
        hop: dict[str, Any] = {
            "id": h.id or str(_uuid.uuid4()),
            "protocol": h.protocol,
            "serverId": h.serverId,
        }
        if h.protocol == "openvpn" and h.transport in ("udp", "tcp"):
            hop["transport"] = h.transport
        if h.protocol == "xray" and h.xrayBypass:
            hop["xrayBypass"] = True
        out.append(hop)
    return out


def _delete_stale_config(conn, server_id: str, protocol: str) -> None:
    rows = conn.execute(
        """
        SELECT id, status FROM configs
        WHERE server_id = ? AND protocol = ?
        """,
        (server_id, protocol),
    ).fetchall()
    for row in rows:
        if row["status"] == "active":
            continue
        if row["status"] == "deploying" and is_deploy_active(row["id"]):
            continue
        conn.execute("DELETE FROM configs WHERE id = ?", (row["id"],))


@app.post("/api/chains/test-route")
def test_chain_route_api(body: DeployChainRequest) -> dict[str, Any]:
    hops = _hop_payload(body.hops)
    try:
        return test_chain_route(
            hops,
            get_server_row=_get_server_row,
            server_target=_server_target,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/configs/deploy-chain")
def deploy_chain_config(body: DeployChainRequest) -> dict[str, Any]:
    hops = _hop_payload(body.hops)
    validate_chain(hops)

    server_ids = list(dict.fromkeys(h["serverId"] for h in hops))
    servers: dict[str, Any] = {}
    targets: dict[str, ServerTarget] = {}

    for sid in server_ids:
        row = _get_server_row(sid)
        servers[sid] = row
        targets[sid] = _server_target(row)

    try:
        runners = verify_chain_servers(servers, targets, prepare_hosts=True)
    except ChainPreflightError as exc:
        raise HTTPException(400, str(exc)) from exc

    server_hosts = {sid: servers[sid]["host"] for sid in server_ids}
    plan = build_plan(hops, server_hosts)
    entry_server_id = plan.entry.server_id
    config_id = new_id()

    try:
        recheck_runners_online(runners)
    except ChainPreflightError as exc:
        raise HTTPException(400, str(exc)) from exc

    recover_stuck_deploying()

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _assert_servers_available_for_deploy(conn, server_ids)
        _delete_stale_config(conn, entry_server_id, plan.entry.protocol)
        conn.execute(
            """
            INSERT INTO configs (id, server_id, protocol, status, meta_json, created_at)
            VALUES (?, ?, ?, 'deploying', ?, ?)
            """,
            (
                config_id,
                entry_server_id,
                plan.entry.protocol,
                json.dumps({"hops": hops, "hopDeployStatus": ["deploying"] + ["waiting"] * (len(hops) - 1), "chain": True}),
                db._now(),
            ),
        )
        cfg_row = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()

    start_chain_deploy_job(
        config_id,
        hops=hops,
        server_ids=server_ids,
        runners=runners,
        plan=plan,
        get_server_row=_get_server_row,
        server_target=_server_target,
    )
    return row_to_config(cfg_row)


@app.post("/api/configs/deploy")
def deploy_config(body: DeployRequest) -> dict[str, Any]:
    row = _get_server_row(body.serverId)
    protocol = body.protocol.lower()
    if protocol not in SINGLE_PROTOCOLS:
        raise HTTPException(400, f"Unsupported protocol: {protocol}")

    transport: str | None = None
    if protocol == "openvpn":
        transport = (body.transport or "udp").lower()
        if transport not in ("udp", "tcp"):
            raise HTTPException(400, "OpenVPN transport must be udp or tcp")

    target = _server_target(row)
    ssh_ok, ssh_err = test_ssh_auth(target)
    if not ssh_ok:
        raise HTTPException(400, ssh_err or "SSH недоступен")

    runner = RemoteRunner(target)
    if row["status"] != "online":
        try:
            ensure_host_ready(runner)
        except Exception as exc:
            raise HTTPException(400, f"Подготовка VPS: {exc}") from exc

    config_id = new_id()
    log.info("Deploy %s on %s (%s) config=%s", protocol, row["name"], row["host"], config_id)

    recover_stuck_deploying()

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _server_has_active_config(conn, body.serverId):
            raise HTTPException(409, "Сервер занят активной цепью или VPN — выберите другой VPS")
        existing = conn.execute(
            "SELECT id, status FROM configs WHERE server_id = ? AND protocol = ?",
            (body.serverId, protocol),
        ).fetchone()
        if existing and existing["status"] in ("active", "deploying"):
            cfg_row = conn.execute("SELECT * FROM configs WHERE id = ?", (existing["id"],)).fetchone()
            if existing["status"] == "active":
                log.info("Deploy %s on %s — already active, returning existing config", protocol, row["name"])
            return row_to_config(cfg_row)
        _delete_stale_config(conn, body.serverId, protocol)
        conn.execute(
            """
            INSERT INTO configs (id, server_id, protocol, status, created_at)
            VALUES (?, ?, ?, 'deploying', ?)
            """,
            (config_id, body.serverId, protocol, db._now()),
        )
        cfg_row = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()

    start_single_deploy_job(
        config_id,
        server_id=body.serverId,
        protocol=protocol,
        transport=transport,
        xray_bypass=body.xrayBypass,
        host=row["host"],
        target=target,
        get_server_row=_get_server_row,
        server_target=_server_target,
    )
    return row_to_config(cfg_row)


@app.post("/api/configs/{config_id}/stop")
def stop_config(config_id: str) -> dict[str, Any]:
    cfg, _row = _get_config_and_server(config_id)
    _assert_active_chain_immutable(cfg)
    if cfg["instance_path"] or json.loads(cfg["meta_json"] or "{}").get("chain"):
        stop_remote_config(
            dict(cfg),
            get_server_row=_get_server_row,
            server_target=_server_target,
            purge=False,
        )
    with connect() as conn:
        conn.execute("UPDATE configs SET status='inactive' WHERE id=?", (config_id,))
        updated = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    return row_to_config(updated)


@app.post("/api/configs/{config_id}/restart")
def restart_config(config_id: str) -> dict[str, Any]:
    cfg, _row = _get_config_and_server(config_id)
    _assert_active_chain_immutable(cfg)
    meta = json.loads(cfg["meta_json"] or "{}")
    if not cfg["instance_path"] and not meta.get("chain"):
        raise HTTPException(400, "No instance path")
    try:
        restart_remote_config(
            dict(cfg),
            get_server_row=_get_server_row,
            server_target=_server_target,
        )
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    with connect() as conn:
        conn.execute("UPDATE configs SET status='active' WHERE id=?", (config_id,))
        updated = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    return row_to_config(updated)


@app.post("/api/configs/{config_id}/repair-xray-bypass")
def repair_xray_bypass_config(config_id: str) -> dict[str, Any]:
    from hoplyra.xray_bypass import (
        extract_server_reality_secrets,
        find_reality_vless_inbound,
        patch_reality_inbound,
        public_key_from_private,
        rebuild_bypass_bundle_from_secrets,
    )

    cfg, row = _get_config_and_server(config_id)
    _assert_active_chain_immutable(cfg)
    meta = json.loads(cfg["meta_json"] or "{}")

    if cfg["protocol"] != "xray" or not meta.get("xrayBypass"):
        raise HTTPException(400, "Конфигурация не использует Xray REALITY bypass")
    if not cfg["instance_path"]:
        raise HTTPException(400, "Конфиг ещё не развёрнут")

    runner = RemoteRunner(_server_target(row))
    config_name = "entry-config.json" if meta.get("chain") else "config.json"
    config_path = f"{cfg['instance_path']}/{config_name}"
    code, out, err = runner.run(f"cat {config_path}")
    if code != 0 or not out.strip():
        raise HTTPException(500, err or f"Не удалось прочитать {config_name} на сервере")

    try:
        server_cfg = json.loads(out)
        vless_uuid, private_key, short_id = extract_server_reality_secrets(server_cfg)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"Некорректный server config: {exc}") from exc

    public_key = meta.get("realityPublicKey") or public_key_from_private(private_key)
    port = int(meta.get("listenPort") or 443)
    remark = f"Chain-{_chain_tag(config_id)}" if meta.get("chain") else None
    new_server, client_text, new_meta = rebuild_bypass_bundle_from_secrets(
        row["host"],
        config_id,
        vless_uuid=vless_uuid,
        private_key=private_key,
        public_key=public_key,
        short_id=short_id,
        port=port,
        name=remark,
    )

    if meta.get("chain"):
        inbound = find_reality_vless_inbound(server_cfg)
        patch_reality_inbound(inbound)
        runner.upload_text(config_path, json.dumps(server_cfg, indent=2))
    else:
        runner.upload_text(config_path, json.dumps(new_server, indent=2))

    container = cfg["container_name"] or (
        f"cv-xentry-{config_id[:8]}" if meta.get("chain") else f"cv-xray-{config_id[:8]}"
    )
    runner.run(f"podman restart {container} 2>/dev/null || podman restart cv-xentry-{config_id[:8]} 2>/dev/null || true")

    merged_meta = {**meta, **new_meta}
    with connect() as conn:
        conn.execute(
            "UPDATE configs SET client_config=?, meta_json=? WHERE id=?",
            (client_text, json.dumps(merged_meta), config_id),
        )
        updated = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()

    return row_to_config(updated)


def _assert_socks_proxy_allowed(cfg: Any) -> dict[str, Any]:
    if cfg["status"] != "active":
        raise HTTPException(400, "SOCKS5 прокси доступен только для активных VPN и цепей")
    if not cfg["instance_path"]:
        raise HTTPException(400, "Конфиг ещё не развёрнут")
    meta = _config_meta(cfg)
    if is_deploy_active(cfg["id"]):
        raise HTTPException(409, "Дождитесь завершения деплоя")
    return meta


@app.post("/api/configs/{config_id}/socks/enable")
def enable_config_socks_proxy(config_id: str) -> dict[str, Any]:
    from hoplyra.socks_proxy import SocksProxyError, enable_socks_proxy

    cfg, row = _get_config_and_server(config_id)
    meta = _assert_socks_proxy_allowed(cfg)
    runner = RemoteRunner(_server_target(row))

    try:
        socks_meta = enable_socks_proxy(
            config_id=config_id,
            instance_path=cfg["instance_path"],
            container_name=cfg["container_name"],
            entry_host=row["host"],
            runner=runner,
            meta=meta,
            protocol=cfg["protocol"],
        )
    except SocksProxyError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.exception("Enable SOCKS proxy failed config=%s", config_id)
        raise HTTPException(500, str(exc)) from exc

    password_once = socks_meta.pop("_password_once", None)
    merged = {**meta, "socksProxy": socks_meta}
    with connect() as conn:
        conn.execute(
            "UPDATE configs SET meta_json=? WHERE id=?",
            (json.dumps(merged), config_id),
        )
        updated = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    result = row_to_config(updated)
    if password_once and result.get("socksProxy"):
        result["socksProxy"] = {**result["socksProxy"], "password": password_once}
    return result


@app.post("/api/configs/{config_id}/socks/disable")
def disable_config_socks_proxy(config_id: str) -> dict[str, Any]:
    from hoplyra.socks_proxy import SocksProxyError, disable_socks_proxy

    cfg, row = _get_config_and_server(config_id)
    meta = _assert_socks_proxy_allowed(cfg)
    if not (meta.get("socksProxy") or {}).get("enabled"):
        return row_to_config(cfg)

    runner = RemoteRunner(_server_target(row))
    try:
        disable_socks_proxy(
            config_id=config_id,
            instance_path=cfg["instance_path"],
            container_name=cfg["container_name"],
            runner=runner,
            meta=meta,
            protocol=cfg["protocol"],
        )
    except SocksProxyError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.exception("Disable SOCKS proxy failed config=%s", config_id)
        raise HTTPException(500, str(exc)) from exc

    merged = {**meta}
    merged.pop("socksProxy", None)
    with connect() as conn:
        conn.execute(
            "UPDATE configs SET meta_json=? WHERE id=?",
            (json.dumps(merged), config_id),
        )
        updated = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    return row_to_config(updated)


@app.post("/api/configs/{config_id}/repair-amnezia-awg")
def repair_amnezia_awg_config(config_id: str) -> dict[str, Any]:
    cfg, row = _get_config_and_server(config_id)
    meta = json.loads(cfg["meta_json"] or "{}")

    if not config_uses_awg(cfg["protocol"], meta):
        raise HTTPException(400, "Конфигурация не использует AmneziaWG")
    _assert_active_chain_immutable(cfg)
    if meta.get("chain"):
        raise HTTPException(400, "Repair AWG недоступен для цепочек")
    if not cfg["instance_path"] or not cfg["client_config"]:
        raise HTTPException(400, "Конфиг ещё не развёрнут")

    runner = RemoteRunner(_server_target(row))
    port = int(meta.get("listenPort") or 55424)
    try:
        repaired = repair_awg_for_amnezia(
            runner,
            cfg["instance_path"],
            cfg["client_config"],
            host=row["host"],
            port=port,
            description=f"Hoplyra {row['name']}",
        )
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

    meta.update(repaired)
    with connect() as conn:
        conn.execute(
            """
            UPDATE configs SET client_config=?, meta_json=? WHERE id=?
            """,
            (repaired["clientConfig"], json.dumps(meta), config_id),
        )
        updated = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()

    return row_to_config(updated)


@app.post("/api/configs/{config_id}/upgrade-awg")
def upgrade_awg_config(config_id: str) -> dict[str, Any]:
    cfg, _row = _get_config_and_server(config_id)
    meta = json.loads(cfg["meta_json"] or "{}")

    if not config_uses_awg(cfg["protocol"], meta):
        raise HTTPException(400, "Конфигурация не использует AmneziaWG")

    is_chain = bool(meta.get("chain"))
    if is_chain:
        raise HTTPException(400, "Upgrade AWG недоступен для цепочек — активные цепи не изменяются")

    if cfg["status"] not in ("active", "inactive"):
        raise HTTPException(400, "Обновление доступно только для развёрнутых конфигов")

    server_ids = awg_server_ids(cfg["protocol"], cfg["server_id"], meta)
    if not server_ids:
        raise HTTPException(400, "Нет серверов с AmneziaWG")

    chain_tag = None
    results: list[dict[str, Any]] = []

    for sid in server_ids:
        srow = _get_server_row(sid)
        target = _server_target(srow)
        ssh_ok, ssh_err = test_ssh_auth(target)
        if not ssh_ok:
            raise HTTPException(400, f"SSH недоступен для {srow['host']}: {ssh_err}")
        runner = RemoteRunner(target)
        try:
            info = upgrade_awg_on_host(
                runner,
                config_id=config_id,
                chain_tag=chain_tag,
                instance_only=True,
            )
        except Exception as exc:
            raise HTTPException(500, f"{srow['host']}: {exc}") from exc
        results.append({"serverId": sid, "host": srow["host"], **info})

    with connect() as conn:
        conn.execute("UPDATE configs SET status='active' WHERE id=?", (config_id,))
        updated = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()

    return {"config": row_to_config(updated), "upgrade": results}


def _cleanup_remote_config_async(cfg: dict[str, Any], *, strict: bool) -> None:
    config_id = cfg["id"]

    def _worker() -> None:
        try:
            stop_remote_config(
                cfg,
                get_server_row=_get_server_row,
                server_target=_server_target,
                purge=True,
                strict=strict,
            )
            log.info("Remote cleanup done config=%s", config_id)
        except Exception as exc:
            log.warning("Remote cleanup failed config=%s: %s", config_id, exc)

    threading.Thread(
        target=_worker,
        daemon=True,
        name=f"hoplyra-delete-{config_id[:8]}",
    ).start()


@app.delete("/api/configs/{config_id}")
def delete_config(config_id: str) -> dict[str, bool]:
    from hoplyra.deploy_cancel import request_deploy_cancel
    from hoplyra.deploy_jobs import is_deploy_active, wait_deploy_inactive

    log.info("Delete config requested: %s", config_id)

    if is_deploy_active(config_id):
        request_deploy_cancel(config_id)
        wait_deploy_inactive(config_id, timeout=15.0)

    with connect() as conn:
        cfg = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
        if not cfg:
            log.info("Delete config %s: already absent from DB", config_id)
            return {"ok": True}
        cfg_dict = dict(cfg)
        strict_teardown = cfg_dict.get("status") == "active"
        conn.execute("DELETE FROM configs WHERE id = ?", (config_id,))

    _cleanup_remote_config_async(cfg_dict, strict=strict_teardown)
    log.info("Delete config %s removed from DB (async cleanup started)", config_id)
    return {"ok": True}


def _get_config_and_server(config_id: str) -> tuple[Any, Any]:
    with connect() as conn:
        cfg = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
        if not cfg:
            raise HTTPException(404, "Config not found")
        row = conn.execute("SELECT * FROM servers WHERE id = ?", (cfg["server_id"],)).fetchone()
    if not row:
        raise HTTPException(404, "Server not found")
    return cfg, row


@app.get("/api/configs/{config_id}/client-config")
def get_client_config(config_id: str) -> dict[str, str]:
    cfg, row = _get_config_and_server(config_id)
    client_config = cfg["client_config"]

    if cfg["protocol"] == "openvpn" and client_config and "PKI ещё" in client_config:
        meta = json.loads(cfg["meta_json"] or "{}")
        deployer = get_deployer_for_config("openvpn", meta)
        runner = RemoteRunner(_server_target(row))
        refreshed = deployer._build_client_ovpn(runner, cfg["instance_path"], row["host"])
        if refreshed and "PKI ещё" not in refreshed:
            with connect() as conn:
                conn.execute("UPDATE configs SET client_config=? WHERE id=?", (refreshed, config_id))
            client_config = refreshed

    if not client_config:
        raise HTTPException(404, "Client config not ready yet")
    return {"protocol": cfg["protocol"], "config": client_config}


def _frontend_dist() -> Path | None:
    env_ui = os.environ.get("HOPLYRA_UI_DIST", "").strip()
    if env_ui:
        candidate = Path(env_ui)
        if candidate.is_dir():
            return candidate

    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        for candidate in (bundle_root / "ui" / "dist", bundle_root / "frontend" / "dist"):
            if candidate.is_dir():
                return candidate

    base = Path(__file__).resolve().parent.parent
    for candidate in (base.parent / "frontend" / "dist", base / "ui" / "dist"):
        if candidate.is_dir():
            return candidate
    return None


def _mount_frontend(app: FastAPI) -> None:
    dist = _frontend_dist()
    if dist is None:
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/{spa_path:path}", include_in_schema=False)
    @app.head("/{spa_path:path}", include_in_schema=False)
    def spa_fallback(spa_path: str) -> FileResponse:
        if spa_path.startswith("api"):
            raise HTTPException(404)
        candidate = dist / spa_path
        if spa_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(
            dist / "index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )


_mount_frontend(app)
