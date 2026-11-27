from __future__ import annotations

import json
import logging
import re
import secrets
import shlex
import time
import urllib.parse
import uuid
from pathlib import PurePosixPath
from typing import Any

from hoplyra import deploy_log
from hoplyra.container_images import ensure_image
from hoplyra.db import (
    connect,
    new_id,
    row_to_realm_broker,
    row_to_realm_tunnel,
)
from hoplyra.protocols import endpoint_host, generate_self_signed_cert_bundle
from hoplyra.remote import RemoteRunner, mkdir_remote, podman_compose_down, podman_compose_up

log = logging.getLogger(__name__)

DEFAULT_BROKER_PORT = 8443
PUBLIC_BROKER_URL = "realm://public@realm.hy2.io"
REALM_TUNNEL_USERNAME = "primary"


class RealmError(Exception):
    pass


from datetime import datetime, timezone


def _parse_iso_time(ts_str: str) -> datetime | None:
    if not ts_str:
        return None
    clean = ts_str.strip().strip('"').strip("'")
    if not clean:
        return None
    clean = re.sub(r"\s+[A-Z]{3,4}$", "", clean)
    if " " in clean and ("+" in clean or "-" in clean[10:]):
        parts = clean.split(" ")
        if len(parts) == 3:
            clean = f"{parts[0]}T{parts[1]}{parts[2]}"
    clean = re.sub(r"(\.\d{6})\d+", r"\1", clean)
    try:
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_uuid(val: str | None) -> str:
    if val is None or not str(val).strip():
        return new_id()
    try:
        return str(uuid.UUID(str(val).strip()))
    except Exception as exc:
        raise RealmError(f"Invalid UUID: {val}") from exc


def _validate_safe_realm_path(instance_path: str, expected_prefix: str) -> None:
    path = PurePosixPath(instance_path)
    if not path.is_absolute():
        raise RealmError(f"Instance path must be absolute: {instance_path}")
    if path.parent != PurePosixPath("/opt/hoplyra"):
        raise RealmError(f"Instance path parent must be /opt/hoplyra, got {path.parent}")
    if not path.name.startswith(expected_prefix):
        raise RealmError(f"Instance directory name must start with '{expected_prefix}', got '{path.name}'")


def compose_runtime_to_engine(runtime: Any) -> str | None:
    if not isinstance(runtime, str) or not runtime.strip():
        return None
    r = runtime.strip().lower()
    if "docker" in r:
        return "docker"
    if "podman" in r:
        return "podman"
    return None


def _detect_container_engine(runner: RemoteRunner, preferred_runtime: str | None = None) -> str:
    """Return 'docker' or 'podman' based on preferred runtime or available engine on VPS."""
    engine = compose_runtime_to_engine(preferred_runtime)
    if engine:
        return engine
    try:
        from hoplyra.remote import _detect_compose_runtime
        comp_rt = _detect_compose_runtime(runner)
        eng = compose_runtime_to_engine(comp_rt)
        if eng:
            return eng
    except Exception:
        pass
    code_d, out_d, _ = runner.run("docker info >/dev/null 2>&1 && echo ok || true", silent=True)
    if "ok" in out_d:
        return "docker"
    code_p, out_p, _ = runner.run("command -v podman >/dev/null 2>&1 && echo ok || true", silent=True)
    if "ok" in out_p:
        return "podman"
    return "docker"


def _detect_compose_variant_for_engine(runner: RemoteRunner, engine: str) -> str:
    """Determine the available compose command for the given container engine.

    If no supported compose variant is available on VPS for this engine,
    raises RealmError.
    """
    if engine == "podman":
        code_sub, _, _ = runner.run("podman compose version >/dev/null 2>&1", timeout=15, silent=True)
        if code_sub == 0:
            return "podman compose"
        code_dash, _, _ = runner.run("command -v podman-compose >/dev/null 2>&1", timeout=15, silent=True)
        if code_dash == 0:
            return "podman-compose"
        raise RealmError("Compose unavailable for Podman: neither 'podman compose' nor 'podman-compose' is accessible on VPS")
    elif engine == "docker":
        code_sub, _, _ = runner.run("docker compose version >/dev/null 2>&1", timeout=15, silent=True)
        if code_sub == 0:
            return "docker compose"
        raise RealmError("Compose unavailable for Docker: 'docker compose' is not accessible on VPS")
    raise RealmError(f"Unsupported container engine '{engine}'")


def _resolve_and_persist_runtime(
    runner: RemoteRunner,
    container: str,
    table: str,
    record_id: str,
) -> str | None:
    """Find which container engine actually owns `container` on VPS, and persist it to SQLite.

    If container is found in multiple engines (both podman and docker), raises RealmError
    without making any changes.
    If container is found in one engine but another engine check failed (daemon error,
    permission denied, etc.), raises RealmError without binding runtime or making changes.
    """
    q_container = shlex.quote(container)
    c_lower = container.lower()
    daemon_error_markers = ("cannot connect", "connection refused", "is the docker daemon running", "permission denied")
    not_found_markers = ("no such container", "no such object", "no container with name", "no container, image, volume")

    found_engines: list[str] = []
    failed_checks: dict[str, str] = {}

    for eng in ("podman", "docker"):
        code, out, err = runner.run(f"{eng} inspect -f '{{{{.State.Status}}}}' {q_container}", timeout=10)
        combined = f"{out}\n{err}".strip().lower()

        if code == 0 and out.strip():
            found_engines.append(eng)
        else:
            if code == 127 or any(bad in combined for bad in ("command not found", f"{eng}: not found")):
                continue
            if any(marker in combined for marker in daemon_error_markers):
                detail = (err or out or "daemon/permission error").strip()
                failed_checks[eng] = detail
                continue
            if any(marker in combined for marker in not_found_markers) or (
                c_lower in combined and any(m in combined for m in ("not found", "could not find"))
            ):
                continue
            detail = (err or out or f"exit code {code}").strip()
            failed_checks[eng] = detail

    if len(found_engines) > 1:
        raise RealmError(
            f"Container '{container}' exists in multiple engines ({', '.join(found_engines)}); ambiguous ownership"
        )

    if failed_checks:
        failed_eng, detail = next(iter(failed_checks.items()))
        if found_engines:
            raise RealmError(
                f"Cannot resolve ownership of container '{container}': found in {found_engines[0]}, "
                f"but {failed_eng} check failed ({detail}); ambiguous ownership"
            )
        is_daemon = any(marker in detail.lower() for marker in daemon_error_markers)
        err_kind = "runtime daemon error" if is_daemon else "runtime check error"
        raise RealmError(
            f"Cannot resolve ownership of container '{container}': cannot confirm container '{container}' absence: "
            f"{err_kind} in {failed_eng}: {detail}"
        )

    if not found_engines:
        return None

    detected_engine = found_engines[0]
    runtime_str = _detect_compose_variant_for_engine(runner, detected_engine)
    try:
        with connect() as conn:
            row = conn.execute(f"SELECT meta_json, runtime FROM {table} WHERE id = ?", (record_id,)).fetchone()
            if row:
                meta = json.loads(row["meta_json"] or "{}")
                meta["runtime"] = runtime_str
                conn.execute(
                    f"UPDATE {table} SET runtime = ?, meta_json = ? WHERE id = ?",
                    (runtime_str, json.dumps(meta), record_id),
                )
    except Exception as exc:
        log.warning("Failed to persist resolved runtime %s for %s (%s): %s", runtime_str, table, record_id, exc)

    return runtime_str




def _is_container_confirmed_stopped_or_absent(
    runner: RemoteRunner,
    container: str,
    runtime: str | None = None,
) -> tuple[bool, str]:
    target_engine = compose_runtime_to_engine(runtime)
    q_container = shlex.quote(container)
    c_lower = container.lower()
    daemon_error_markers = ("cannot connect", "connection refused", "is the docker daemon running", "permission denied")
    not_found_markers = ("no such container", "no such object", "no container with name", "no container, image, volume")

    if target_engine:
        inspect_cmd = f"{target_engine} inspect -f '{{{{.State.Running}}}}' {q_container}"
        code, out, err = runner.run(inspect_cmd, timeout=10)
        combined = f"{out}\n{err}".strip().lower()

        if code == 0:
            val = out.strip().lower()
            if val in ("false", "exited", "dead", "stopped"):
                return True, ""
            if val == "true" or "running" in val:
                return False, f"container '{container}' is still running in {target_engine}"
            return False, f"unexpected inspect state '{val}' for container '{container}'"

        if code == 127 or any(bad in combined for bad in ("command not found", f"{target_engine}: not found")):
            detail = (err or out or "command not found").strip()
            return False, f"runtime engine '{target_engine}' is unavailable on host: {detail}"

        if any(marker in combined for marker in daemon_error_markers):
            detail = (err or out or "daemon unavailable").strip()
            return False, f"runtime daemon error while inspecting container '{container}': {detail}"

        if any(marker in combined for marker in not_found_markers) or (
            c_lower in combined and any(m in combined for m in ("not found", "could not find"))
        ):
            return True, ""

        detail = (err or out or f"exit code {code}").strip()
        return False, f"runtime error inspecting container '{container}': {detail}"

    # Saved runtime is unknown (legacy / migrated record with runtime=NULL).
    # Inspect candidate engines (podman, docker). Do NOT accept absence in one engine as proof
    # if another engine might host the container or if inspect failed.
    candidate_engines = ("podman", "docker")
    results: dict[str, tuple[str, str]] = {}

    for eng in candidate_engines:
        inspect_cmd = f"{eng} inspect -f '{{{{.State.Running}}}}' {q_container}"
        code, out, err = runner.run(inspect_cmd, timeout=10)
        combined = f"{out}\n{err}".strip().lower()

        if code == 0:
            val = out.strip().lower()
            if val in ("false", "exited", "dead", "stopped"):
                results[eng] = ("stopped", "")
            elif val == "true" or "running" in val:
                results[eng] = ("running", f"container '{container}' is still running in {eng}")
            else:
                results[eng] = ("other_error", f"unexpected inspect state '{val}' in {eng}")
        else:
            if code == 127 or any(bad in combined for bad in ("command not found", f"{eng}: not found")):
                results[eng] = ("engine_missing", (err or out or "command not found").strip())
            elif any(marker in combined for marker in daemon_error_markers):
                results[eng] = ("daemon_error", (err or out or "daemon unavailable").strip())
            elif any(marker in combined for marker in not_found_markers) or (
                c_lower in combined and any(m in combined for m in ("not found", "could not find"))
            ):
                results[eng] = ("not_found", "")
            else:
                results[eng] = ("other_error", (err or out or f"exit code {code}").strip())

    # 1. If any engine reports the container is still running, abort deletion immediately!
    for eng, (kind, detail) in results.items():
        if kind == "running":
            return False, detail

    # 2. If any engine reports the container is confirmed stopped (and no engine reported running):
    for eng, (kind, _) in results.items():
        if kind == "stopped":
            return True, ""

    # 3. If any engine had a daemon error or unexpected error, we cannot prove absence:
    for eng, (kind, detail) in results.items():
        if kind == "daemon_error":
            return False, f"cannot confirm container '{container}' absence: runtime daemon error in {eng}: {detail}"
        if kind == "other_error":
            return False, f"cannot confirm container '{container}' absence: runtime error in {eng}: {detail}"


    # 4. Check if at least one engine confirmed not_found and no other engine had errors
    confirmed_absent = any(kind == "not_found" for kind, _ in results.values())
    if confirmed_absent:
        return True, ""

    return False, f"cannot confirm state of container '{container}' across candidate engines: {results}"



def wait_broker_ready(
    runner: RemoteRunner,
    container_name: str,
    listen_port: int,
    timeout: int = 15,
    runtime: str | None = None,
) -> None:
    deadline = time.time() + timeout
    q_container = shlex.quote(container_name)
    engine = compose_runtime_to_engine(runtime) or _detect_container_engine(runner)
    last_logs = ""
    while time.time() < deadline:
        inspect_cmd = f"{engine} inspect -f '{{{{.State.Status}}}} {{{{json .State.StartedAt}}}}' {q_container} 2>/dev/null"
        code, out, _ = runner.run(inspect_cmd, timeout=10)
        if code != 0:
            time.sleep(1)
            continue

        lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
        if not lines:
            time.sleep(1)
            continue
        parts = lines[0].split(None, 1)
        status = parts[0].lower() if parts else ""
        started_at_raw = parts[1].strip() if len(parts) > 1 else ""
        started_dt = _parse_iso_time(started_at_raw)

        if status in ("exited", "dead"):
            _, logs, _ = runner.run(f"{engine} logs --tail 50 {q_container} 2>&1", timeout=10)
            raise RealmError(f"Broker container exited unexpectedly with status '{status}': {logs.strip()}")

        if status != "running":
            time.sleep(1)
            continue

        # StartedAt must be non-empty and successfully parsed to verify current run
        if not started_at_raw or started_dt is None:
            time.sleep(1)
            continue

        clean_started_at = started_at_raw.strip('"').strip("'")
        logs_cmd = f"{engine} logs -t --since {shlex.quote(clean_started_at)} {q_container} 2>&1"
        _, logs, _ = runner.run(logs_cmd, timeout=10)
        last_logs = logs.strip()

        has_new_listening_log = False
        for line in last_logs.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            line_parts = line_str.split(None, 1)
            line_dt = _parse_iso_time(line_parts[0]) if line_parts else None
            # Unknown timestamp format or timestamps older than StartedAt must NOT be accepted
            if line_dt is None or line_dt < started_dt:
                continue

            msg = line_parts[1].lower() if len(line_parts) > 1 else ""
            if any(bad in msg for bad in ("not listening", "stopped listening", "failed", "error")):
                continue

            # Positive marker in the actual log format of hysteria-realm-server
            if (
                "hysteria realm server listening on" in msg
                or "server listening on" in msg
                or "listening on" in msg
                or 'msg="listening"' in msg
                or '"msg":"listening"' in msg
            ):
                has_new_listening_log = True
                break

        if has_new_listening_log:
            return

        time.sleep(1)

    raise RealmError(
        f"Broker failed to become ready within {timeout}s on port {listen_port}. Last logs: {last_logs}"
    )


def wait_tunnel_ready(
    runner: RemoteRunner,
    container_name: str,
    realm_name: str,
    timeout: int = 15,
    runtime: str | None = None,
) -> None:
    deadline = time.time() + timeout
    q_container = shlex.quote(container_name)
    engine = compose_runtime_to_engine(runtime) or _detect_container_engine(runner)
    last_logs = ""
    while time.time() < deadline:
        inspect_cmd = f"{engine} inspect -f '{{{{.State.Status}}}} {{{{json .State.StartedAt}}}}' {q_container} 2>/dev/null"
        code, out, _ = runner.run(inspect_cmd, timeout=10)
        if code != 0:
            time.sleep(1)
            continue

        lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
        if not lines:
            time.sleep(1)
            continue
        parts = lines[0].split(None, 1)
        status = parts[0].lower() if parts else ""
        started_at_raw = parts[1].strip() if len(parts) > 1 else ""
        started_dt = _parse_iso_time(started_at_raw)

        if status in ("exited", "dead"):
            _, logs, _ = runner.run(f"{engine} logs --tail 50 {q_container} 2>&1", timeout=10)
            raise RealmError(f"Tunnel container exited unexpectedly with status '{status}': {logs.strip()}")

        if status != "running":
            time.sleep(1)
            continue

        # StartedAt must be non-empty and successfully parsed to verify current run
        if not started_at_raw or started_dt is None:
            time.sleep(1)
            continue

        clean_started_at = started_at_raw.strip('"').strip("'")
        logs_cmd = f"{engine} logs -t --since {shlex.quote(clean_started_at)} {q_container} 2>&1"
        _, logs, _ = runner.run(logs_cmd, timeout=10)
        last_logs = logs.strip()

        for line in last_logs.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            line_parts = line_str.split(None, 1)
            line_dt = _parse_iso_time(line_parts[0]) if line_parts else None
            # Unknown timestamp format or timestamps older than StartedAt must NOT be accepted
            if line_dt is None or line_dt < started_dt:
                continue

            msg = line_parts[1].lower() if len(line_parts) > 1 else ""

            if "panic:" in msg or "fatal" in msg:
                raise RealmError(f"Tunnel container failed: {line_str}")

            if "realm registered" in msg:
                return

        time.sleep(1)

    raise RealmError(
        f"Tunnel failed to register room '{realm_name}' within {timeout}s. Last logs: {last_logs}"
    )



# ---------------------------------------------------------------------------
# Broker Management (Self-Hosted Rendezvous Server)
# ---------------------------------------------------------------------------

def get_active_broker() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM realms_brokers WHERE status = 'active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM realms_brokers ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return row_to_realm_broker(row) if row else None


def list_brokers() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM realms_brokers ORDER BY created_at DESC").fetchall()
        return [row_to_realm_broker(r) for r in rows]


def deploy_realm_broker(
    runner: RemoteRunner,
    server: dict[str, Any],
    listen_port: int = DEFAULT_BROKER_PORT,
    token: str | None = None,
    domain_or_ip: str | None = None,
    broker_id: str | None = None,
    async_deploy: bool = False,
) -> dict[str, Any]:
    broker_id = _normalize_uuid(broker_id)
    srv_label = server.get("name") or server.get("host") or server["id"]
    log.info("Deploying realm broker %s on server %s:%s (async=%s)", broker_id, srv_label, listen_port, async_deploy)
    deploy_log.clear(broker_id)
    deploy_log.mark_active(broker_id)
    deploy_log.publish(broker_id, f"Deploying Rendezvous Broker on {srv_label}:{listen_port}", stage="init")

    secret_token = token.strip() if token and token.strip() else secrets.token_urlsafe(24)
    target_host = domain_or_ip.strip() if domain_or_ip and domain_or_ip.strip() else server["host"]
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"
    container_name = f"cv-realm-broker-{broker_id}"
    compose_project = f"cv-realm-broker-{broker_id}"

    broker_record = {
        "id": broker_id,
        "server_id": server["id"],
        "listen_port": listen_port,
        "token": secret_token,
        "domain_or_ip": target_host,
        "status": "deploying" if async_deploy else "active",
        "container_name": container_name,
        "instance_path": instance_path,
        "runtime": None,
        "meta_json": json.dumps({"compose_project": compose_project, "server_name": server.get("name", "")}),
        "created_at": _now(),
    }

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (:id, :server_id, :listen_port, :token, :domain_or_ip, :status, :container_name, :instance_path, :runtime, :meta_json, :created_at)
            """,
            broker_record,
        )

    def _execute() -> None:
        try:
            deploy_log.publish(broker_id, f"Creating broker workspace at {instance_path}", stage="config")
            mkdir_remote(runner, f"{instance_path}/certs")

            compose_content = f"""services:
  hysteria-realm-server:
    image: hoplyra-realm-server:1
    container_name: {container_name}
    restart: unless-stopped
    environment:
      HYSTERIA_REALM_TOKEN: {json.dumps(secret_token)}
      HYSTERIA_REALM_LISTEN: ":{listen_port}"
    ports:
      - "{listen_port}:{listen_port}/tcp"
      - "{listen_port}:{listen_port}/udp"
    volumes:
      - {json.dumps(instance_path + "/certs:/etc/hysteria-realm")}
"""
            runner.upload_text(f"{instance_path}/docker-compose.yml", compose_content)

            deploy_log.publish(broker_id, "Checking container image hoplyra-realm-server:1", stage="image")
            ensure_image(runner, "hoplyra-realm-server:1", "hysteria-realm-server")

            used_runtime = podman_compose_up(runner, instance_path, compose_project)
            if isinstance(used_runtime, str) and used_runtime.strip():
                runtime_str = used_runtime.strip()
            else:
                try:
                    from hoplyra.remote import _detect_compose_runtime
                    runtime_str = _detect_compose_runtime(runner)
                except Exception:
                    runtime_str = None


            with connect() as conn:
                row = conn.execute("SELECT meta_json FROM realms_brokers WHERE id = ?", (broker_id,)).fetchone()
                meta = json.loads(row["meta_json"] or "{}") if row else {}
                meta["compose_project"] = compose_project
                meta["server_name"] = server.get("name", "")
                if runtime_str:
                    meta["runtime"] = runtime_str
                conn.execute(
                    "UPDATE realms_brokers SET runtime = ?, meta_json = ? WHERE id = ?",
                    (runtime_str, json.dumps(meta), broker_id),
                )

            deploy_log.publish(broker_id, f"Waiting for broker readiness on port {listen_port}", stage="container")
            wait_broker_ready(runner, container_name, listen_port, runtime=runtime_str)

            with connect() as conn:
                conn.execute("UPDATE realms_brokers SET status = 'active' WHERE id = ?", (broker_id,))

            log.info("Realm broker %s successfully deployed on port %s", broker_id, listen_port)
            deploy_log.publish(broker_id, f"Rendezvous Broker listening on port {listen_port}", level="success", stage="finish")
            deploy_log.mark_finished(broker_id, True)
        except Exception as exc:
            log.exception("Realm broker deployment failed for %s: %s", broker_id, exc)
            with connect() as conn:
                conn.execute("UPDATE realms_brokers SET status = 'error' WHERE id = ?", (broker_id,))
            deploy_log.publish(broker_id, f"Broker deployment error: {exc}", level="error", stage="error")
            deploy_log.mark_finished(broker_id, False)
            if not async_deploy:
                raise

    if async_deploy:
        snapshot = get_broker_by_id(broker_id) or broker_record
        import threading
        t = threading.Thread(target=_execute, daemon=False, name=f"hoplyra-broker-{broker_id[:8]}")
        t.start()
        return snapshot

    _execute()
    return get_broker_by_id(broker_id) or broker_record


def get_broker_by_id(broker_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM realms_brokers WHERE id = ?", (broker_id,)).fetchone()
        return row_to_realm_broker(row) if row else None


def stop_realm_broker(broker_id: str, runner: RemoteRunner) -> None:
    broker = get_broker_by_id(broker_id)
    if not broker:
        raise RealmError("Broker not found")
    log.info("Stopping realm broker %s", broker_id)
    project = broker["meta"].get("compose_project", f"cv-realm-broker-{broker_id}")
    saved_runtime = broker.get("runtime") or broker.get("meta", {}).get("runtime")
    if not saved_runtime:
        saved_runtime = _resolve_and_persist_runtime(
            runner, broker.get("containerName") or f"cv-realm-broker-{broker_id}", "realms_brokers", broker_id
        )
    try:
        podman_compose_down(runner, broker["instancePath"], project, strict=True, runtime=saved_runtime)
    except Exception as exc:
        with connect() as conn:
            conn.execute("UPDATE realms_brokers SET status = 'error' WHERE id = ?", (broker_id,))
        raise RealmError(f"Failed to stop realm broker {broker_id}: {exc}")
    with connect() as conn:
        conn.execute("UPDATE realms_brokers SET status = 'inactive' WHERE id = ?", (broker_id,))
    log.info("Realm broker %s stopped", broker_id)


def start_realm_broker(broker_id: str, runner: RemoteRunner) -> None:
    broker = get_broker_by_id(broker_id)
    if not broker:
        raise RealmError("Broker not found")
    log.info("Starting realm broker %s", broker_id)
    project = broker["meta"].get("compose_project", f"cv-realm-broker-{broker_id}")
    container = broker.get("containerName") or f"cv-realm-broker-{broker_id}"
    listen_port = broker.get("listenPort") or 8443
    saved_runtime = broker.get("runtime") or broker.get("meta", {}).get("runtime")
    if not saved_runtime:
        saved_runtime = _resolve_and_persist_runtime(runner, container, "realms_brokers", broker_id)
        if not saved_runtime:
            absent, reason = _is_container_confirmed_stopped_or_absent(runner, container, runtime=None)
            if not absent:
                with connect() as conn:
                    conn.execute("UPDATE realms_brokers SET status = 'error' WHERE id = ?", (broker_id,))
                raise RealmError(f"Cannot start broker {broker_id}: container state cannot be verified ({reason})")
    used_runtime = podman_compose_up(runner, broker["instancePath"], project, runtime=saved_runtime)

    runtime_str = used_runtime if isinstance(used_runtime, str) and used_runtime.strip() else saved_runtime
    try:
        wait_broker_ready(runner, container, listen_port, timeout=15, runtime=runtime_str)
    except Exception as exc:
        log.error("Broker %s failed readiness check on start: %s", broker_id, exc)
        with connect() as conn:
            conn.execute("UPDATE realms_brokers SET status = 'error' WHERE id = ?", (broker_id,))
        raise RealmError(f"Broker failed to become ready on start: {exc}")
    with connect() as conn:
        row = conn.execute("SELECT meta_json FROM realms_brokers WHERE id = ?", (broker_id,)).fetchone()
        meta = json.loads(row["meta_json"] or "{}") if row else {}
        if runtime_str:
            meta["runtime"] = runtime_str
        conn.execute(
            "UPDATE realms_brokers SET status = 'active', runtime = ?, meta_json = ? WHERE id = ?",
            (runtime_str, json.dumps(meta), broker_id),
        )
    log.info("Realm broker %s started", broker_id)


def delete_realm_broker(broker_id: str, runner: RemoteRunner) -> None:
    broker = get_broker_by_id(broker_id)
    if not broker:
        return
    log.info("Deleting realm broker %s", broker_id)
    instance_path = broker.get("instancePath") or f"/opt/hoplyra/realm-broker-{broker_id}"
    project = broker["meta"].get("compose_project", f"cv-realm-broker-{broker_id}")
    container = broker.get("containerName") or f"cv-realm-broker-{broker_id}"
    saved_runtime = broker.get("runtime") or broker.get("meta", {}).get("runtime")
    if not saved_runtime:
        try:
            saved_runtime = _resolve_and_persist_runtime(runner, container, "realms_brokers", broker_id)
        except RealmError as exc:
            if "ambiguous ownership" not in str(exc):
                with connect() as conn:
                    conn.execute("UPDATE realms_brokers SET status = 'error' WHERE id = ?", (broker_id,))
            raise
    q_container = shlex.quote(container)
    _validate_safe_realm_path(instance_path, "realm-broker-")

    try:
        podman_compose_down(runner, instance_path, project, strict=True, runtime=saved_runtime)
    except Exception as exc:
        log.warning("Failed to down broker compose %s: %s", broker_id, exc)
        with connect() as conn:
            conn.execute("UPDATE realms_brokers SET status = 'error' WHERE id = ?", (broker_id,))
        raise RealmError(f"Failed to delete realm broker {broker_id}: compose down failed: {exc}")

    stopped, reason = _is_container_confirmed_stopped_or_absent(runner, container, runtime=saved_runtime)
    if not stopped:
        with connect() as conn:
            conn.execute("UPDATE realms_brokers SET status = 'error' WHERE id = ?", (broker_id,))
        raise RealmError(f"Failed to delete realm broker {broker_id}: {reason}")

    try:
        code, _, err = runner.run(f"rm -rf -- {shlex.quote(instance_path)}")
        if code != 0:
            with connect() as conn:
                conn.execute("UPDATE realms_brokers SET status = 'error' WHERE id = ?", (broker_id,))
            raise RealmError(f"Failed to delete realm broker {broker_id}: cleanup failed with code {code}: {err.strip()}")
    except Exception as exc:
        log.warning("Failed to cleanup broker path %s: %s", instance_path, exc)
        with connect() as conn:
            conn.execute("UPDATE realms_brokers SET status = 'error' WHERE id = ?", (broker_id,))
        raise RealmError(f"Failed to delete realm broker {broker_id}: cleanup failed: {exc}")

    with connect() as conn:
        conn.execute("DELETE FROM realms_brokers WHERE id = ?", (broker_id,))
    log.info("Realm broker %s deleted", broker_id)



def get_broker_logs(broker_id: str, runner: RemoteRunner, tail: int = 150) -> str:
    broker = get_broker_by_id(broker_id)
    if not broker:
        raise RealmError("Broker not found")
    saved_runtime = broker.get("runtime") or broker.get("meta", {}).get("runtime")
    if not saved_runtime:
        container = broker.get("containerName") or f"cv-realm-broker-{broker_id}"
        saved_runtime = _resolve_and_persist_runtime(runner, container, "realms_brokers", broker_id)
    engine = compose_runtime_to_engine(saved_runtime) or _detect_container_engine(runner)
    container = broker.get("containerName") or f"cv-realm-broker-{broker_id}"
    code, stdout, stderr = runner.run(
        f"{engine} logs --tail {int(tail)} {shlex.quote(container)} 2>&1"
    )
    logs = (stdout or stderr or "").strip()
    return logs or "No logs available"


# ---------------------------------------------------------------------------
# Realm Tunnel Management (P2P Rooms)
# ---------------------------------------------------------------------------

def list_tunnels() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM realms_tunnels ORDER BY created_at DESC").fetchall()
        return [row_to_realm_tunnel(r) for r in rows]


def get_tunnel_by_id(tunnel_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM realms_tunnels WHERE id = ?", (tunnel_id,)).fetchone()
        return row_to_realm_tunnel(row) if row else None


def build_tunnel_client_material(
    broker_url: str,
    realm_name: str,
    auth_password: str,
    pin_sha256: str,
    tunnel_name: str,
    username: str = REALM_TUNNEL_USERNAME,
) -> dict[str, str]:
    """Generates YAML config, realm URI, and Hysteria 2 share URI."""
    full_realm_endpoint = f"{broker_url.rstrip('/')}/{realm_name}"
    credential = f"{username}:{auth_password}"

    # Standard client YAML config
    yaml_config = f"""server: {json.dumps(full_realm_endpoint)}
auth: {json.dumps(credential)}
tls:
  sni: bing.com
  pinSHA256: {json.dumps(pin_sha256)}
fastOpen: true
bandwidth:
  up: 50 mbps
  down: 200 mbps
"""
    # Hysteria 2 URI for mobile clients (sing-box, Matsuri, Nekobox, etc.)
    parsed = urllib.parse.urlparse(broker_url)
    broker_host = parsed.netloc.split("@")[-1] if "@" in parsed.netloc else parsed.netloc
    broker_token = parsed.username or "public"

    safe_name = urllib.parse.quote(f"Hoplyra-Realm-{tunnel_name}")
    safe_auth = urllib.parse.quote(credential, safe="")

    # Standard Hysteria 2 URI with realm parameter
    share_uri = (
        f"hysteria2://{safe_auth}@{broker_host}/"
        f"?realm={urllib.parse.quote(realm_name)}&token={urllib.parse.quote(broker_token)}"
        f"&insecure=1&sni=bing.com&pinSHA256={pin_sha256}#{safe_name}"
    )

    return {
        "yaml": yaml_config,
        "realmEndpoint": full_realm_endpoint,
        "shareUri": share_uri,
    }


def deploy_realm_tunnel(
    runner: RemoteRunner,
    server: dict[str, Any],
    name: str,
    broker_type: str = "public",
    custom_broker_url: str | None = None,
    realm_name: str | None = None,
    auth_password: str | None = None,
    tunnel_id: str | None = None,
    async_deploy: bool = False,
) -> dict[str, Any]:
    tunnel_id = _normalize_uuid(tunnel_id)
    tunnel_name = name.strip() if name and name.strip() else f"Tunnel-{tunnel_id[:8]}"
    srv_label = server.get("name") or server.get("host") or server["id"]
    log.info("Deploying realm tunnel %s ('%s') on server %s (async=%s)", tunnel_id, tunnel_name, srv_label, async_deploy)
    deploy_log.clear(tunnel_id)
    deploy_log.mark_active(tunnel_id)
    deploy_log.publish(tunnel_id, f"Deploying room '{tunnel_name}' on server {srv_label}", stage="init")

    if broker_type == "self_hosted":
        active_broker = get_active_broker()
        if not active_broker:
            raise RealmError("No active self-hosted broker found. Please deploy or configure a broker first.")
        host = active_broker["domainOrIp"] or "127.0.0.1"
        port = active_broker["listenPort"]
        token = active_broker["token"]
        broker_url = f"realm://{token}@{endpoint_host(host)}:{port}"
    elif broker_type == "custom" and custom_broker_url and custom_broker_url.strip():
        broker_url = custom_broker_url.strip()
    else:
        broker_type = "public"
        broker_url = PUBLIC_BROKER_URL

    clean_realm_name = realm_name.strip() if realm_name and realm_name.strip() else f"hoplyra-{secrets.token_hex(8)}"
    clean_auth_password = auth_password.strip() if auth_password and auth_password.strip() else secrets.token_urlsafe(18)

    cert_pem, key_pem, pin_sha256 = generate_self_signed_cert_bundle("bing.com")

    instance_path = f"/opt/hoplyra/realm-tunnel-{tunnel_id}"
    container_name = f"cv-realm-tun-{tunnel_id}"
    compose_project = f"cv-realm-tun-{tunnel_id}"

    materials = build_tunnel_client_material(
        broker_url=broker_url,
        realm_name=clean_realm_name,
        auth_password=clean_auth_password,
        pin_sha256=pin_sha256,
        tunnel_name=tunnel_name,
        username=REALM_TUNNEL_USERNAME,
    )

    meta = {
        "compose_project": compose_project,
        "server_name": server.get("name", ""),
        "shareUri": materials["shareUri"],
        "realmEndpoint": materials["realmEndpoint"],
    }

    record = {
        "id": tunnel_id,
        "server_id": server["id"],
        "name": tunnel_name,
        "realm_name": clean_realm_name,
        "broker_type": broker_type,
        "broker_url": broker_url,
        "auth_password": clean_auth_password,
        "pin_sha256": pin_sha256,
        "status": "deploying" if async_deploy else "active",
        "container_name": container_name,
        "instance_path": instance_path,
        "runtime": None,
        "client_config": materials["yaml"],
        "meta_json": json.dumps(meta),
        "created_at": _now(),
    }

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_tunnels (
                id, server_id, name, realm_name, broker_type, broker_url, auth_password,
                pin_sha256, status, container_name, instance_path, runtime, client_config, meta_json, created_at
            ) VALUES (
                :id, :server_id, :name, :realm_name, :broker_type, :broker_url, :auth_password,
                :pin_sha256, :status, :container_name, :instance_path, :runtime, :client_config, :meta_json, :created_at
            )
            """,
            record,
        )

    def _execute() -> None:
        try:
            deploy_log.publish(tunnel_id, f"Using broker {broker_url} with room '{clean_realm_name}'", stage="broker")
            deploy_log.publish(tunnel_id, f"Creating room workspace at {instance_path}", stage="config")
            mkdir_remote(runner, instance_path)
            runner.upload_text(f"{instance_path}/server.crt", cert_pem)
            runner.upload_text(f"{instance_path}/server.key", key_pem)

            full_realm_listen = f"{broker_url.rstrip('/')}/{clean_realm_name}"

            server_config_yaml = f"""listen: {json.dumps(full_realm_listen)}
tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key
auth:
  type: userpass
  userpass:
    {REALM_TUNNEL_USERNAME}: {json.dumps(clean_auth_password)}
masquerade:
  type: proxy
  proxy:
    url: https://bing.com/
    rewriteHost: true
bandwidth:
  up: 100 mbps
  down: 500 mbps
"""
            runner.upload_text(f"{instance_path}/config.yaml", server_config_yaml)

            # In realm mode, no host ports need to be published since the container initiates outbound QUIC to the broker!
            compose_yaml = f"""services:
  hysteria2:
    image: hoplyra-hysteria2:1
    container_name: {container_name}
    restart: unless-stopped
    volumes:
      - {json.dumps(instance_path + ":/etc/hysteria")}
"""
            runner.upload_text(f"{instance_path}/docker-compose.yml", compose_yaml)

            deploy_log.publish(tunnel_id, "Checking container image hoplyra-hysteria2:1", stage="image")
            ensure_image(runner, "hoplyra-hysteria2:1", "hysteria2")

            used_runtime = podman_compose_up(runner, instance_path, compose_project)
            if isinstance(used_runtime, str) and used_runtime.strip():
                runtime_str = used_runtime.strip()
            else:
                try:
                    from hoplyra.remote import _detect_compose_runtime
                    runtime_str = _detect_compose_runtime(runner)
                except Exception:
                    runtime_str = None


            with connect() as conn:
                row = conn.execute("SELECT meta_json FROM realms_tunnels WHERE id = ?", (tunnel_id,)).fetchone()
                meta_dict = json.loads(row["meta_json"] or "{}") if row else {}
                if runtime_str:
                    meta_dict["runtime"] = runtime_str
                conn.execute(
                    "UPDATE realms_tunnels SET runtime = ?, meta_json = ? WHERE id = ?",
                    (runtime_str, json.dumps(meta_dict), tunnel_id),
                )

            deploy_log.publish(tunnel_id, f"Waiting for room '{clean_realm_name}' registration", stage="container")
            wait_tunnel_ready(runner, container_name, clean_realm_name, runtime=runtime_str)

            with connect() as conn:
                conn.execute("UPDATE realms_tunnels SET status = 'active' WHERE id = ?", (tunnel_id,))

            log.info("Realm tunnel %s ('%s') successfully deployed", tunnel_id, tunnel_name)
            deploy_log.publish(tunnel_id, f"Room '{tunnel_name}' is active and listening via Rendezvous", level="success", stage="finish")
            deploy_log.mark_finished(tunnel_id, True)
        except Exception as exc:
            log.exception("Realm tunnel deployment failed for %s: %s", tunnel_id, exc)
            with connect() as conn:
                conn.execute("UPDATE realms_tunnels SET status = 'error' WHERE id = ?", (tunnel_id,))
            deploy_log.publish(tunnel_id, f"Tunnel deployment error: {exc}", level="error", stage="error")
            deploy_log.mark_finished(tunnel_id, False)
            if not async_deploy:
                raise

    if async_deploy:
        snapshot = get_tunnel_by_id(tunnel_id) or record
        import threading
        t = threading.Thread(target=_execute, daemon=False, name=f"hoplyra-tunnel-{tunnel_id[:8]}")
        t.start()
        return snapshot

    _execute()
    return get_tunnel_by_id(tunnel_id) or record


def stop_realm_tunnel(tunnel_id: str, runner: RemoteRunner) -> None:
    tunnel = get_tunnel_by_id(tunnel_id)
    if not tunnel:
        raise RealmError("Tunnel not found")
    log.info("Stopping realm tunnel %s", tunnel_id)
    project = tunnel["meta"].get("compose_project", f"cv-realm-tun-{tunnel_id}")
    saved_runtime = tunnel.get("runtime") or tunnel.get("meta", {}).get("runtime")
    if not saved_runtime:
        saved_runtime = _resolve_and_persist_runtime(
            runner, tunnel.get("containerName") or f"cv-realm-tun-{tunnel_id}", "realms_tunnels", tunnel_id
        )
    try:
        podman_compose_down(runner, tunnel["instancePath"], project, strict=True, runtime=saved_runtime)
    except Exception as exc:
        with connect() as conn:
            conn.execute("UPDATE realms_tunnels SET status = 'error' WHERE id = ?", (tunnel_id,))
        raise RealmError(f"Failed to stop realm tunnel {tunnel_id}: {exc}")
    with connect() as conn:
        conn.execute("UPDATE realms_tunnels SET status = 'inactive' WHERE id = ?", (tunnel_id,))
    log.info("Realm tunnel %s stopped", tunnel_id)


def start_realm_tunnel(tunnel_id: str, runner: RemoteRunner) -> None:
    tunnel = get_tunnel_by_id(tunnel_id)
    if not tunnel:
        raise RealmError("Tunnel not found")
    log.info("Starting realm tunnel %s", tunnel_id)
    project = tunnel["meta"].get("compose_project", f"cv-realm-tun-{tunnel_id}")
    container = tunnel.get("containerName") or f"cv-realm-tun-{tunnel_id}"
    realm_name = tunnel.get("realmName", "")
    saved_runtime = tunnel.get("runtime") or tunnel.get("meta", {}).get("runtime")
    if not saved_runtime:
        saved_runtime = _resolve_and_persist_runtime(runner, container, "realms_tunnels", tunnel_id)
        if not saved_runtime:
            absent, reason = _is_container_confirmed_stopped_or_absent(runner, container, runtime=None)
            if not absent:
                with connect() as conn:
                    conn.execute("UPDATE realms_tunnels SET status = 'error' WHERE id = ?", (tunnel_id,))
                raise RealmError(f"Cannot start tunnel {tunnel_id}: container state cannot be verified ({reason})")
    used_runtime = podman_compose_up(runner, tunnel["instancePath"], project, runtime=saved_runtime)

    runtime_str = used_runtime if isinstance(used_runtime, str) and used_runtime.strip() else saved_runtime
    try:
        wait_tunnel_ready(runner, container, realm_name, timeout=15, runtime=runtime_str)
    except Exception as exc:
        log.error("Tunnel %s failed readiness check on start: %s", tunnel_id, exc)
        with connect() as conn:
            conn.execute("UPDATE realms_tunnels SET status = 'error' WHERE id = ?", (tunnel_id,))
        raise RealmError(f"Tunnel failed to become ready on start: {exc}")
    with connect() as conn:
        row = conn.execute("SELECT meta_json FROM realms_tunnels WHERE id = ?", (tunnel_id,)).fetchone()
        meta = json.loads(row["meta_json"] or "{}") if row else {}
        if runtime_str:
            meta["runtime"] = runtime_str
        conn.execute(
            "UPDATE realms_tunnels SET status = 'active', runtime = ?, meta_json = ? WHERE id = ?",
            (runtime_str, json.dumps(meta), tunnel_id),
        )
    log.info("Realm tunnel %s started", tunnel_id)


def delete_realm_tunnel(tunnel_id: str, runner: RemoteRunner) -> None:
    tunnel = get_tunnel_by_id(tunnel_id)
    if not tunnel:
        return
    log.info("Deleting realm tunnel %s", tunnel_id)
    instance_path = tunnel.get("instancePath") or f"/opt/hoplyra/realm-tunnel-{tunnel_id}"
    project = tunnel["meta"].get("compose_project", f"cv-realm-tun-{tunnel_id}")
    container = tunnel.get("containerName") or f"cv-realm-tun-{tunnel_id}"
    saved_runtime = tunnel.get("runtime") or tunnel.get("meta", {}).get("runtime")
    if not saved_runtime:
        try:
            saved_runtime = _resolve_and_persist_runtime(runner, container, "realms_tunnels", tunnel_id)
        except RealmError as exc:
            if "ambiguous ownership" not in str(exc):
                with connect() as conn:
                    conn.execute("UPDATE realms_tunnels SET status = 'error' WHERE id = ?", (tunnel_id,))
            raise
    q_container = shlex.quote(container)
    _validate_safe_realm_path(instance_path, "realm-tunnel-")

    try:
        podman_compose_down(runner, instance_path, project, strict=True, runtime=saved_runtime)
    except Exception as exc:
        log.warning("Failed to down tunnel compose %s: %s", tunnel_id, exc)
        with connect() as conn:
            conn.execute("UPDATE realms_tunnels SET status = 'error' WHERE id = ?", (tunnel_id,))
        raise RealmError(f"Failed to delete realm tunnel {tunnel_id}: compose down failed: {exc}")

    stopped, reason = _is_container_confirmed_stopped_or_absent(runner, container, runtime=saved_runtime)
    if not stopped:
        with connect() as conn:
            conn.execute("UPDATE realms_tunnels SET status = 'error' WHERE id = ?", (tunnel_id,))
        raise RealmError(f"Failed to delete realm tunnel {tunnel_id}: {reason}")

    try:
        code, _, err = runner.run(f"rm -rf -- {shlex.quote(instance_path)}")
        if code != 0:
            with connect() as conn:
                conn.execute("UPDATE realms_tunnels SET status = 'error' WHERE id = ?", (tunnel_id,))
            raise RealmError(f"Failed to delete realm tunnel {tunnel_id}: cleanup failed with code {code}: {err.strip()}")
    except Exception as exc:
        log.warning("Failed to cleanup tunnel path %s: %s", instance_path, exc)
        with connect() as conn:
            conn.execute("UPDATE realms_tunnels SET status = 'error' WHERE id = ?", (tunnel_id,))
        raise RealmError(f"Failed to delete realm tunnel {tunnel_id}: cleanup failed: {exc}")

    with connect() as conn:
        conn.execute("DELETE FROM realms_tunnels WHERE id = ?", (tunnel_id,))
    log.info("Realm tunnel %s deleted", tunnel_id)



def get_tunnel_logs(tunnel_id: str, runner: RemoteRunner, tail: int = 150) -> str:
    tunnel = get_tunnel_by_id(tunnel_id)
    if not tunnel:
        raise RealmError("Tunnel not found")
    saved_runtime = tunnel.get("runtime") or tunnel.get("meta", {}).get("runtime")
    if not saved_runtime:
        container = tunnel.get("containerName") or f"cv-realm-tun-{tunnel_id}"
        saved_runtime = _resolve_and_persist_runtime(runner, container, "realms_tunnels", tunnel_id)
    engine = compose_runtime_to_engine(saved_runtime) or _detect_container_engine(runner)
    container = tunnel.get("containerName") or f"cv-realm-tun-{tunnel_id}"
    code, stdout, stderr = runner.run(
        f"{engine} logs --tail {int(tail)} {shlex.quote(container)} 2>&1"
    )
    logs = (stdout or stderr or "").strip()
    return logs or "No logs available"
