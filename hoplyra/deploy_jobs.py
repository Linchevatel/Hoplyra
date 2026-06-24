from __future__ import annotations

import json
import logging
import os
import threading
import time
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


def recover_stuck_deploying() -> None:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM configs WHERE status = 'deploying'").fetchall()

    for row in rows:
        config_id = row["id"]
        if is_deploy_active(config_id):
            log.info("Skip recover for active deploy config=%s", config_id)
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
        with connect() as conn:
            conn.execute(
                "UPDATE configs SET status='error', meta_json=? WHERE id=? AND status='deploying'",
                (json.dumps(meta), config_id),
            )
        log.warning("Recovered stuck deploying config=%s", config_id)


def schedule_recover_stuck_deploying() -> None:
    def _worker() -> None:
        time.sleep(RECOVER_GRACE_SEC)
        recover_stuck_deploying()

    threading.Thread(
        target=_worker,
        daemon=True,
        name="hoplyra-deploy-recover",
    ).start()


def _touched_chain_server_ids(cfg: dict[str, Any]) -> list[str] | None:
    meta = json.loads(cfg.get("meta_json") or "{}")
    if not meta.get("chain"):
        return None
    hops = meta.get("hops") or []
    statuses = meta.get("hopDeployStatus") or []
    touched: list[str] = []
    seen: set[str] = set()
    for index, hop in enumerate(hops):
        status = statuses[index] if index < len(statuses) else "waiting"
        if status in ("deploying", "done", "error"):
            server_id = hop.get("serverId")
            if server_id and server_id not in seen:
                seen.add(server_id)
                touched.append(server_id)
    return touched or None


def _rollback_deploy_artifacts(
    cfg_dict: dict[str, Any],
    *,
    get_server_row: Callable[[str], Any],
    server_target: Callable[[Any], ServerTarget],
) -> str | None:
    server_ids = _touched_chain_server_ids(cfg_dict)
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
    _stamp_deploy_job(config_id)
    try:
        runner()
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
    host: str,
    target: ServerTarget,
    get_server_row: Callable[[str], Any],
    server_target: Callable[[Any], ServerTarget],
) -> None:
    def _job() -> None:
        try:
            deployer = get_deployer(protocol, transport=transport, xray_bypass=xray_bypass)
            runner = RemoteRunner(target)
            result = deployer.deploy(runner, config_id, host)
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

    thread = threading.Thread(target=_runner, daemon=False, name=name)
    _register_thread(thread)
    thread.start()


def start_single_deploy_job(
    config_id: str,
    *,
    server_id: str,
    protocol: str,
    transport: str | None,
    xray_bypass: bool,
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
            deployer = ChainDeployer(str(DATA_DIR))
            result = deployer.deploy(runners, plan, config_id, on_hop_status=on_hop_statuses)
            meta = {
                "chain": True,
                "hops": hops,
                "hopDeployStatus": ["done"] * len(hops),
                **result.meta,
                **(result.bypass_meta or {}),
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
            )
        except Exception as exc:
            log.exception("Chain deploy failed config=%s: %s", config_id, exc)
            _mark_deploy_failed(
                config_id,
                exc,
                get_server_row=get_server_row,
                server_target=server_target,
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
