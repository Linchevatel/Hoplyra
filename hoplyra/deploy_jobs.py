from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable

from hoplyra import db
from hoplyra.chains.deployer import ChainDeployer
from hoplyra.chains.preflight import recheck_runners_online
from hoplyra.config_lifecycle import stop_remote_config
from hoplyra.db import DATA_DIR, connect
from hoplyra.deploy_cancel import DeployCancelled, clear_deploy_cancel
from hoplyra.protocols import get_deployer
from hoplyra.remote import RemoteRunner, ServerTarget

log = logging.getLogger("hoplyra")

_ACTIVE_LOCK = threading.Lock()
_ACTIVE_THREADS: list[threading.Thread] = []
_ACTIVE_CONFIG_IDS: set[str] = set()

OWNER_TOKEN = str(uuid.uuid4())
LEASE_SEC = 60.0
HEARTBEAT_SEC = 15.0


def claim_deploy(conn, config_id: str) -> None:
    """Called inside the queue insertion transaction."""
    conn.execute("UPDATE configs SET deploy_owner=?, lease_expires_at=? WHERE id=? AND status='deploying'",
                 (OWNER_TOKEN, time.time() + LEASE_SEC, config_id))


def renew_deploy_lease(config_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE configs SET lease_expires_at=? WHERE id=? AND deploy_owner=? AND status='deploying'",
                     (time.time() + LEASE_SEC, config_id, OWNER_TOKEN))


def release_deploy_lease(config_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE configs SET deploy_owner=NULL, lease_expires_at=NULL WHERE id=? AND deploy_owner=?",
                     (config_id, OWNER_TOKEN))


RECOVER_GRACE_SEC = float(os.environ.get("HOPLYRA_DEPLOY_RECOVER_GRACE_SEC", "12"))


def _register_thread(thread: threading.Thread) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_THREADS.append(thread)


def _unregister_thread(thread: threading.Thread) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_THREADS[:] = [t for t in _ACTIVE_THREADS if t is not thread]


def register_active_deploy(config_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CONFIG_IDS.add(config_id)


def unregister_active_deploy(config_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CONFIG_IDS.discard(config_id)


@contextmanager
def reserve_deploy(config_id: str):
    """Own a queued attempt before its row is committed or worker is started."""
    register_active_deploy(config_id)
    try:
        yield
    except BaseException:
        try:
            with connect() as conn:
                conn.execute("DELETE FROM configs WHERE id=? AND status='deploying'", (config_id,))
        finally:
            unregister_active_deploy(config_id)
            clear_deploy_cancel(config_id)
        raise


def is_deploy_active(config_id: str) -> bool:
    with _ACTIVE_LOCK:
        return config_id in _ACTIVE_CONFIG_IDS


def wait_deploy_inactive(config_id: str, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while is_deploy_active(config_id) and time.time() < deadline:
        time.sleep(0.25)
    return not is_deploy_active(config_id)


def wait_for_deploy_jobs(timeout: float = 120) -> None:
    with _ACTIVE_LOCK:
        threads = [t for t in _ACTIVE_THREADS if t.is_alive()]
    for thread in threads:
        thread.join(timeout=timeout)
        if thread.is_alive():
            log.warning("Deploy thread %s still running after shutdown wait", thread.name)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def _stamp_deploy_job(config_id: str) -> None:
    with connect() as conn:
        row = conn.execute("SELECT meta_json FROM configs WHERE id = ?", (config_id,)).fetchone()
        if not row:
            return
        meta = json.loads(row["meta_json"] or "{}")
        meta["deployPid"] = os.getpid()
        meta["deployStartedAt"] = db._now()
        conn.execute("UPDATE configs SET meta_json=? WHERE id=?", (json.dumps(meta), config_id))


def recover_stuck_deploying(config_ids: set[str] | None = None) -> None:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT * FROM configs WHERE status = 'deploying'").fetchall()

        for row in rows:
            config_id = row["id"]
            if config_ids is not None and config_id not in config_ids:
                continue
            if is_deploy_active(config_id):
                log.info("Skip recover for active deploy config=%s", config_id)
                continue

            if row["deploy_owner"] and (row["lease_expires_at"] or 0) > time.time():
                continue
            meta = json.loads(row["meta_json"] or "{}")

            meta["statusMessage"] = (
                "Deploy interrupted (service restarted). Remove the failed config or redeploy."
            )
            if meta.get("chain") and meta.get("hopDeployStatus"):
                meta["hopDeployStatus"] = [
                    "error" if status == "deploying" else status
                    for status in meta["hopDeployStatus"]
                ]
            meta.pop("deployPid", None)
            meta.pop("deployStartedAt", None)
            conn.execute(
                "UPDATE configs SET status='error', meta_json=?, deploy_owner=NULL, lease_expires_at=NULL WHERE id=? AND status='deploying'",
                (json.dumps(meta), config_id),
            )
            log.warning("Recovered stuck deploying config=%s", config_id)


def schedule_recover_stuck_deploying() -> None:
    # Only recover rows present at startup, never requests queued during grace.
    with connect() as conn:
        config_ids = {row["id"] for row in conn.execute("SELECT id FROM configs WHERE status='deploying'")}

    def _worker() -> None:
        time.sleep(RECOVER_GRACE_SEC)
        recover_stuck_deploying(config_ids)

    threading.Thread(
        target=_worker,
        daemon=True,
        name="hoplyra-deploy-recover",
    ).start()


def _touched_chain_server_ids(cfg: dict[str, Any]) -> list[str] | None:
    meta = json.loads(cfg.get("meta_json") or "{}")
    if not meta.get("chain"):
        return None
    if "touchedServerIds" in meta:
        return list(dict.fromkeys(meta["touchedServerIds"]))
    # Legacy rows have no reliable ownership: UI statuses hide reverse deployment
    # order. Conservatively clean all chain servers, never infer it from statuses.
    return None


def _rollback_deploy_artifacts(
    cfg_dict: dict[str, Any],
    *,
    get_server_row: Callable[[str], Any],
    server_target: Callable[[Any], ServerTarget],
) -> str | None:
    server_ids = _touched_chain_server_ids(cfg_dict)
    # stop_remote_config treats [] as all servers; do not call it for no ownership.
    if server_ids == []:
        return None
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            stop_remote_config(
                cfg_dict,
                get_server_row=get_server_row,
                server_target=server_target,
                purge=True,
                strict=True,
                server_ids=server_ids,
            )
            return None
        except Exception as exc:
            last_error = exc
            log.warning(
                "Deploy rollback cleanup %s attempt %s failed: %s",
                cfg_dict["id"],
                attempt + 1,
                exc,
            )
    if last_error is None:
        return None
    return str(last_error)[:300]


def _mark_deploy_failed(
    config_id: str,
    error: BaseException | str,
    *,
    get_server_row: Callable[[str], Any],
    server_target: Callable[[Any], ServerTarget],
    touched_server_ids: list[str] | None = None,
) -> None:
    message = str(error).strip()[:500] or "Deploy failed"
    with connect() as conn:
        cfg = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
    if not cfg:
        return

    cfg_dict = dict(cfg)
    meta = json.loads(cfg_dict.get("meta_json") or "{}")
    meta["statusMessage"] = message
    if meta.get("chain") and meta.get("hopDeployStatus"):
        meta["hopDeployStatus"] = [
            "error" if status in ("deploying", "waiting") else status
            for status in meta["hopDeployStatus"]
        ]
    meta.pop("deployPid", None)
    meta.pop("deployStartedAt", None)

    with connect() as conn:
        conn.execute(
            """
            UPDATE configs
            SET status='error', client_config=NULL, container_name=NULL,
                instance_path=NULL, meta_json=?
            WHERE id=?
            """,
            (json.dumps(meta), config_id),
        )
    log.info("Deploy failed config=%s: %s", config_id, message)

    # A running chain attempt knows its ownership even if its first DB write failed.
    # Keep this override local to cleanup so failure reporting can still succeed.
    if touched_server_ids is not None:
        cleanup_meta = json.loads(cfg_dict.get("meta_json") or "{}")
        cleanup_meta["touchedServerIds"] = list(touched_server_ids)
        cfg_dict["meta_json"] = json.dumps(cleanup_meta)

    cleanup_error = _rollback_deploy_artifacts(
        cfg_dict,
        get_server_row=get_server_row,
        server_target=server_target,
    )
    if cleanup_error:
        note = f"{message} (remote cleanup: {cleanup_error})"[:500]
        with connect() as conn:
            row = conn.execute("SELECT meta_json FROM configs WHERE id = ?", (config_id,)).fetchone()
            if row:
                meta2 = json.loads(row["meta_json"] or "{}")
                meta2["statusMessage"] = note
                conn.execute(
                    "UPDATE configs SET meta_json=? WHERE id=?",
                    (json.dumps(meta2), config_id),
                )


def _run_deploy_job(config_id: str, runner: Callable[[], None]) -> None:
    register_active_deploy(config_id)
    stopped = threading.Event()

    def heartbeat() -> None:
        while not stopped.wait(HEARTBEAT_SEC):
            try:
                renew_deploy_lease(config_id)
            except Exception:
                log.exception("Deploy heartbeat failed config=%s", config_id)

    pulse = threading.Thread(target=heartbeat, daemon=True, name="hoplyra-deploy-lease")
    try:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            claim_deploy(conn, config_id)
        _stamp_deploy_job(config_id)
        pulse.start()
        runner()
    finally:
        stopped.set()
        if pulse.ident is not None:
            pulse.join()
        try:
            release_deploy_lease(config_id)
        finally:
            unregister_active_deploy(config_id)
            clear_deploy_cancel(config_id)


def run_single_deploy_job(
    config_id: str,
    *,
    server_id: str,
    protocol: str,
    transport: str | None,
    xray_bypass: bool,
    awg_version: str = "awg3.1",
    awg_params: dict[str, Any] | None = None,
    host: str,
    target: ServerTarget,
    get_server_row: Callable[[str], Any],
    server_target: Callable[[Any], ServerTarget],
) -> None:
    def _job() -> None:
        try:
            deployer = get_deployer(protocol, transport=transport, xray_bypass=xray_bypass)
            runner = RemoteRunner(target)
            result = deployer.deploy(
                runner,
                config_id,
                host,
                awg_version=awg_version,
                awg_params=awg_params,
            )
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE configs SET status='active', client_config=?, container_name=?,
                        instance_path=?, meta_json=? WHERE id=?
                    """,
                    (
                        result.client_config,
                        result.container_name,
                        result.instance_path,
                        json.dumps(result.meta),
                        config_id,
                    ),
                )
                conn.execute(
                    "UPDATE servers SET status='online', last_seen=? WHERE id=?",
                    (db._now(), server_id),
                )
            log.info("Deploy %s done config=%s", protocol, config_id)
        except DeployCancelled:
            log.info("Deploy %s cancelled config=%s", protocol, config_id)
            _mark_deploy_failed(
                config_id,
                "Deploy cancelled",
                get_server_row=get_server_row,
                server_target=server_target,
            )
        except Exception as exc:
            log.exception("Deploy %s failed config=%s: %s", protocol, config_id, exc)
            _mark_deploy_failed(
                config_id,
                exc,
                get_server_row=get_server_row,
                server_target=server_target,
            )

    _run_deploy_job(config_id, _job)


def _start_deploy_thread(name: str, target: Callable[..., None], kwargs: dict[str, Any]) -> None:
    def _runner() -> None:
        try:
            target(**kwargs)
        finally:
            _unregister_thread(thread)

    config_id = kwargs["config_id"]
    register_active_deploy(config_id)
    thread = None
    try:
        thread = threading.Thread(target=_runner, daemon=False, name=name)
        _register_thread(thread)
        thread.start()
    except BaseException:
        if thread is not None:
            _unregister_thread(thread)
        unregister_active_deploy(config_id)
        clear_deploy_cancel(config_id)
        raise


def start_single_deploy_job(
    config_id: str,
    *,
    server_id: str,
    protocol: str,
    transport: str | None,
    xray_bypass: bool,
    awg_version: str = "awg3.1",
    awg_params: dict[str, Any] | None = None,
    host: str,
    target: ServerTarget,
    get_server_row: Callable[[str], Any],
    server_target: Callable[[Any], ServerTarget],
) -> None:
    _start_deploy_thread(
        f"hoplyra-deploy-{config_id[:8]}",
        run_single_deploy_job,
        {
            "config_id": config_id,
            "server_id": server_id,
            "protocol": protocol,
            "transport": transport,
            "xray_bypass": xray_bypass,
            "awg_version": awg_version,
            "awg_params": awg_params,
            "host": host,
            "target": target,
            "get_server_row": get_server_row,
            "server_target": server_target,
        },
    )


def _normalize_hop_statuses(statuses: list[str]) -> list[str]:
    if not statuses:
        return statuses
    if all(s in ("done", "error") for s in statuses):
        return statuses
    active = next((i for i, s in enumerate(statuses) if s == "deploying"), -1)
    if active < 0:
        active = next((i for i, s in enumerate(statuses) if s not in ("done", "error")), -1)
    if active < 0:
        return statuses
    normalized: list[str] = []
    for i, status in enumerate(statuses):
        if status == "error":
            normalized.append("error")
        elif i < active:
            normalized.append("done")
        elif i == active:
            normalized.append("deploying")
        else:
            normalized.append("waiting")
    return normalized


def run_chain_deploy_job(
    config_id: str,
    *,
    hops: list[dict[str, Any]],
    server_ids: list[str],
    runners: dict[str, RemoteRunner],
    plan: Any,
    get_server_row: Callable[[str], Any],
    server_target: Callable[[Any], ServerTarget],
) -> None:
    def _job() -> None:
        touched_server_ids: list[str] = []

        def on_server_touched(server_id: str) -> None:
            if server_id not in touched_server_ids:
                touched_server_ids.append(server_id)
            with connect() as conn:
                row = conn.execute("SELECT meta_json FROM configs WHERE id = ?", (config_id,)).fetchone()
                meta = json.loads(row["meta_json"] or "{}") if row else {}
                meta["touchedServerIds"] = list(touched_server_ids)
                conn.execute("UPDATE configs SET meta_json=? WHERE id=?",
                             (json.dumps(meta), config_id))

        def on_hop_statuses(statuses: list[str]) -> None:
            if len(statuses) != len(hops):
                return
            statuses = _normalize_hop_statuses(statuses)
            with connect() as conn:
                row = conn.execute("SELECT meta_json FROM configs WHERE id = ?", (config_id,)).fetchone()
                meta = json.loads(row["meta_json"] or "{}") if row else {}
                meta.update({"hops": hops, "hopDeployStatus": statuses, "chain": True})
                conn.execute(
                    "UPDATE configs SET meta_json=? WHERE id=?",
                    (json.dumps(meta), config_id),
                )

        try:
            # Persist explicit empty ownership before any provisioning can fail.
            with connect() as conn:
                row = conn.execute("SELECT meta_json FROM configs WHERE id = ?", (config_id,)).fetchone()
                meta = json.loads(row["meta_json"] or "{}") if row else {}
                meta["touchedServerIds"] = []
                conn.execute("UPDATE configs SET meta_json=? WHERE id=?",
                             (json.dumps(meta), config_id))
            deployer = ChainDeployer(str(DATA_DIR))
            result = deployer.deploy(runners, plan, config_id,
                                     on_hop_status=on_hop_statuses,
                                     on_server_touched=on_server_touched)
            meta = {
                "chain": True,
                "hops": hops,
                "hopDeployStatus": ["done"] * len(hops),
                **result.meta,
                **(result.bypass_meta or {}),
                "touchedServerIds": touched_server_ids,
            }
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE configs SET status='active', client_config=?, container_name=?,
                        instance_path=?, meta_json=? WHERE id=?
                    """,
                    (
                        result.client_config,
                        result.container_name,
                        result.instance_path,
                        json.dumps(meta),
                        config_id,
                    ),
                )
                for sid in server_ids:
                    conn.execute(
                        "UPDATE servers SET status='online', last_seen=? WHERE id=?",
                        (db._now(), sid),
                    )
            log.info("Chain deploy done config=%s", config_id)
        except DeployCancelled:
            log.info("Chain deploy cancelled config=%s", config_id)
            _mark_deploy_failed(
                config_id,
                "Deploy cancelled",
                get_server_row=get_server_row,
                server_target=server_target,
                touched_server_ids=touched_server_ids,
            )
        except Exception as exc:
            log.exception("Chain deploy failed config=%s: %s", config_id, exc)
            _mark_deploy_failed(
                config_id,
                exc,
                get_server_row=get_server_row,
                server_target=server_target,
                touched_server_ids=touched_server_ids,
            )

    _run_deploy_job(config_id, _job)


def start_chain_deploy_job(
    config_id: str,
    *,
    hops: list[dict[str, Any]],
    server_ids: list[str],
    runners: dict[str, RemoteRunner],
    plan: Any,
    get_server_row: Callable[[str], Any],
    server_target: Callable[[Any], ServerTarget],
) -> None:
    _start_deploy_thread(
        f"hoplyra-chain-{config_id[:8]}",
        run_chain_deploy_job,
        {
            "config_id": config_id,
            "hops": hops,
            "server_ids": server_ids,
            "runners": runners,
            "plan": plan,
            "get_server_row": get_server_row,
            "server_target": server_target,
        },
    )
