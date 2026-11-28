from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import logging
import re
import secrets
import shlex
import threading
import time
import urllib.parse
from typing import Any, Iterator

from hoplyra import deploy_log
from hoplyra.container_images import ensure_image
from hoplyra.db import connect, new_id, row_to_standalone_proxy
from hoplyra.protocols import endpoint_host, generate_self_signed_cert_bundle
from hoplyra.remote import (
    RemoteRunner,
    _detect_compose_runtime,
    ensure_podman,
    mkdir_remote,
    podman_compose_down,
    podman_compose_up,
)

log = logging.getLogger(__name__)

SUPPORTED_PROXY_TYPES = ("socks5", "https", "mtproto", "shadowsocks")

DEFAULT_PROXY_PORTS: dict[str, int] = {
    "socks5": 1080,
    "https": 8443,
    "mtproto": 8443,
    "shadowsocks": 8388,
}

SS_CIPHER_STANDARD_NAMES: dict[str, str] = {
    "AEAD_CHACHA20_POLY1305": "chacha20-ietf-poly1305",
    "AEAD_AES_256_GCM": "aes-256-gcm",
    "AEAD_AES_128_GCM": "aes-128-gcm",
    "chacha20-ietf-poly1305": "chacha20-ietf-poly1305",
    "aes-256-gcm": "aes-256-gcm",
    "aes-128-gcm": "aes-128-gcm",
}

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_MTPROTO_FAKE_TLS_RE = re.compile(r"^ee[0-9a-f]{34,}$")


class _ProxyLockEntry:
    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.refs = 0


_PROXY_STATE_GUARD = threading.Lock()
_PROXY_LOCKS: dict[str, _ProxyLockEntry] = {}
_ACTIVE_PROXY_DEPLOY_IDS: set[str] = set()
_ACTIVE_PROXY_DEPLOY_THREADS: list[threading.Thread] = []


class StandaloneProxyError(Exception):
    pass


class StandaloneProxyConflictError(StandaloneProxyError):
    """Raised when an operation conflicts with an active deploy or concurrent proxy operation."""
    pass


def _checkout_proxy_lock(proxy_id: str) -> _ProxyLockEntry:
    with _PROXY_STATE_GUARD:
        entry = _PROXY_LOCKS.get(proxy_id)
        if entry is None:
            entry = _ProxyLockEntry()
            _PROXY_LOCKS[proxy_id] = entry
        entry.refs += 1
        return entry


def _checkin_proxy_lock(proxy_id: str, entry: _ProxyLockEntry) -> None:
    with _PROXY_STATE_GUARD:
        entry.refs -= 1
        if entry.refs <= 0 and _PROXY_LOCKS.get(proxy_id) is entry:
            _PROXY_LOCKS.pop(proxy_id, None)


@contextmanager
def _proxy_lock_context(
    proxy_id: str,
    *,
    blocking: bool = True,
    action: str | None = None,
) -> Iterator[None]:
    entry = _checkout_proxy_lock(proxy_id)
    acquired = False
    try:
        acquired = entry.lock.acquire(blocking=blocking)
        if not acquired:
            raise StandaloneProxyConflictError(
                f"Proxy {proxy_id} has another operation in progress; cannot {action or 'proceed'} right now"
            )
        yield
    finally:
        if acquired:
            entry.lock.release()
        _checkin_proxy_lock(proxy_id, entry)


def is_proxy_deploy_active(proxy_id: str) -> bool:
    with _PROXY_STATE_GUARD:
        return proxy_id in _ACTIVE_PROXY_DEPLOY_IDS


def _register_proxy_deploy(proxy_id: str) -> None:
    with _PROXY_STATE_GUARD:
        _ACTIVE_PROXY_DEPLOY_IDS.add(proxy_id)


def _unregister_proxy_deploy(proxy_id: str) -> None:
    with _PROXY_STATE_GUARD:
        _ACTIVE_PROXY_DEPLOY_IDS.discard(proxy_id)


def _start_tracked_proxy_thread(thread: threading.Thread) -> None:
    with _PROXY_STATE_GUARD:
        _ACTIVE_PROXY_DEPLOY_THREADS.append(thread)
        try:
            thread.start()
        except Exception:
            _ACTIVE_PROXY_DEPLOY_THREADS[:] = [t for t in _ACTIVE_PROXY_DEPLOY_THREADS if t is not thread]
            raise


def _unregister_proxy_thread(thread: threading.Thread) -> None:
    with _PROXY_STATE_GUARD:
        _ACTIVE_PROXY_DEPLOY_THREADS[:] = [t for t in _ACTIVE_PROXY_DEPLOY_THREADS if t is not thread]


def wait_for_proxy_deploy_jobs(timeout: float = 120) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        with _PROXY_STATE_GUARD:
            threads = [t for t in _ACTIVE_PROXY_DEPLOY_THREADS if t.is_alive()]
        if not threads:
            return
        for t in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning("Standalone proxy job thread %s still running after shutdown wait", t.name)
                return
            t.join(timeout=remaining)
            if t.is_alive():
                log.warning("Standalone proxy job thread %s still running after shutdown wait", t.name)
                return


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _instance_dir(proxy_id: str, is_local: bool = False) -> str:
    if is_local:
        from hoplyra.db import DATA_DIR

        return str(DATA_DIR / "proxies" / proxy_id)
    return f"/opt/hoplyra/proxies/{proxy_id}"


def _compose_project(proxy_id: str) -> str:
    return f"cv-proxy-{proxy_id[:8]}"


def _container_name(proxy_id: str) -> str:
    return f"cv-prx-{proxy_id[:8]}"


def urllib_quote(s: str, safe: str = "") -> str:
    return urllib.parse.quote(s, safe=safe)


def _compose_escape(value: str) -> str:
    """Escape Compose variable interpolation ($ -> $$) before scalar serialization."""
    return value.replace("$", "$$")


def _yaml_scalar(value: str) -> str:
    """Serialize a scalar string safely for Compose YAML (JSON double-quoted + $$ escaping)."""
    return json.dumps(_compose_escape(value), ensure_ascii=False)


def _render_compose_file(
    *,
    image: str,
    container_name: str,
    ports: list[str],
    command: list[str],
    volumes: list[str] | None = None,
) -> str:
    lines = [
        "services:",
        "  proxy:",
        f"    image: {_yaml_scalar(image)}",
        f"    container_name: {_yaml_scalar(container_name)}",
        "    restart: unless-stopped",
        "    ports:",
    ]
    for port_map in ports:
        lines.append(f"      - {_yaml_scalar(port_map)}")
    if volumes:
        lines.append("    volumes:")
        for vol in volumes:
            lines.append(f"      - {_yaml_scalar(vol)}")
    cmd_serialized = json.dumps([_compose_escape(arg) for arg in command], ensure_ascii=False)
    lines.append(f"    command: {cmd_serialized}")
    return "\n".join(lines) + "\n"


def _normalize_domain(domain: str) -> str:
    raw = domain.strip().lower()
    if not raw:
        raise StandaloneProxyError("SNI domain cannot be empty")
    try:
        ascii_domain = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise StandaloneProxyError(f"Invalid SNI domain: {domain}") from exc
    if not _DOMAIN_RE.fullmatch(ascii_domain):
        raise StandaloneProxyError(f"Invalid SNI domain: {domain}")
    return ascii_domain


def _validate_mtproto_secret(secret: str) -> tuple[str, str]:
    """Validate Fake-TLS ee... secret and return (normalized_secret, decoded_sni_domain)."""
    cand = secret.strip().lower()
    if not _MTPROTO_FAKE_TLS_RE.fullmatch(cand) or len(cand) % 2 != 0:
        raise StandaloneProxyError(
            "Invalid MTProto Fake-TLS secret: expected 'ee' + 32 hex chars + hex-encoded domain"
        )
    try:
        domain_raw = bytes.fromhex(cand[34:]).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise StandaloneProxyError("Invalid MTProto Fake-TLS secret: domain bytes must be ASCII") from exc
    domain_norm = _normalize_domain(domain_raw)
    return cand, domain_norm


def _build_gost_listen_uri(
    scheme: str,
    username_or_cipher: str,
    password: str,
    port: int,
    *,
    query: str | None = None,
) -> str:
    user_enc = urllib_quote(username_or_cipher, safe="")
    pass_enc = urllib_quote(password, safe="")
    base = f"{scheme}://{user_enc}:{pass_enc}@0.0.0.0:{int(port)}"
    if query:
        return f"{base}?{query}"
    return base


def _scan_listening_ports(runner: RemoteRunner) -> set[int]:
    code, out, err = runner.run("ss -H -lntu", timeout=20)
    if code != 0:
        detail = (err or out or f"exit {code}").strip()
        raise StandaloneProxyError(f"Failed to check listening ports on VPS: {detail}")

    busy_ports: set[int] = set()
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] in ("UNCONN", "LISTEN", "ESTAB"):
            if len(parts) < 4:
                continue
            addr = parts[3]
        else:
            if len(parts) < 5:
                continue
            addr = parts[4]
        port_str = addr.rsplit(":", 1)[-1]
        if port_str.isdigit():
            busy_ports.add(int(port_str))
    return busy_ports


def _collect_reserved_ports(
    conn: Any,
    server_id: str,
    *,
    exclude_proxy_id: str | None = None,
) -> set[int]:
    busy_ports: set[int] = set()
    if exclude_proxy_id:
        proxy_rows = conn.execute(
            "SELECT listen_port FROM standalone_proxies WHERE server_id = ? AND status IN ('active', 'deploying') AND id != ? AND listen_port > 0",
            (server_id, exclude_proxy_id),
        ).fetchall()
    else:
        proxy_rows = conn.execute(
            "SELECT listen_port FROM standalone_proxies WHERE server_id = ? AND status IN ('active', 'deploying') AND listen_port > 0",
            (server_id,),
        ).fetchall()
    for row in proxy_rows:
        busy_ports.add(int(row["listen_port"]))

    for row in conn.execute(
        "SELECT listen_port FROM realms_brokers WHERE server_id = ? AND status IN ('active', 'deploying') AND listen_port > 0",
        (server_id,),
    ).fetchall():
        busy_ports.add(int(row["listen_port"]))
    return busy_ports


def _find_free_proxy_port(
    runner: RemoteRunner,
    server_id: str,
    default_port: int,
    proxy_type: str,
    *,
    exclude_proxy_id: str | None = None,
) -> int:
    busy_ports = _scan_listening_ports(runner)
    with connect() as conn:
        busy_ports |= _collect_reserved_ports(conn, server_id, exclude_proxy_id=exclude_proxy_id)

    for p in range(default_port, 65536):
        if p not in busy_ports:
            return p
    raise StandaloneProxyError(f"No free port available for {proxy_type} in range {default_port}-65535")


def _reserve_free_proxy_port(
    runner: RemoteRunner,
    *,
    proxy_id: str,
    server_id: str,
    default_port: int,
    proxy_type: str,
    meta: dict[str, Any],
) -> int:
    ss_busy = _scan_listening_ports(runner)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        db_busy = _collect_reserved_ports(conn, server_id, exclude_proxy_id=proxy_id)
        busy_ports = ss_busy | db_busy
        chosen: int | None = None
        for p in range(default_port, 65536):
            if p not in busy_ports:
                chosen = p
                break
        if chosen is None:
            conn.rollback()
            raise StandaloneProxyError(f"No free port available for {proxy_type} in range {default_port}-65535")
        meta["listenPort"] = chosen
        conn.execute(
            "UPDATE standalone_proxies SET listen_port = ?, meta_json = ? WHERE id = ?",
            (chosen, json.dumps(meta), proxy_id),
        )
        conn.commit()
    return chosen


@contextmanager
def _proxy_operation_guard(proxy_id: str, action: str) -> Iterator[None]:
    with _proxy_lock_context(proxy_id, blocking=False, action=action):
        if is_proxy_deploy_active(proxy_id):
            raise StandaloneProxyConflictError(
                f"Proxy {proxy_id} is currently deploying; cannot {action} until deployment finishes"
            )
        prx = get_standalone_proxy_by_id(proxy_id)
        if prx["status"] == "deploying":
            raise StandaloneProxyConflictError(
                f"Proxy {proxy_id} is currently deploying; cannot {action} until deployment finishes"
            )
        yield


def _wait_proxy_ready(
    runner: RemoteRunner,
    container: str,
    port: int,
    *,
    is_udp: bool = False,
    runtime: str | None = None,
    timeout: int = 15,
) -> None:
    rt_bin = "docker" if (runtime and "docker" in runtime) else "podman"
    container_q = shlex.quote(container)
    port_num = int(port)
    ss_flags = "-H -lun" if is_udp else "-H -ltn"
    check_cmd = (
        f"for i in $(seq 1 {int(timeout * 2)}); do "
        f"  info=$({rt_bin} inspect -f '{{{{.State.Status}}}} {{{{.State.Pid}}}}' {container_q} 2>/dev/null || echo missing); "
        f"  status=${{info%% *}}; "
        f"  pid=${{info#* }}; "
        f"  if [ \"$status\" = 'exited' ] || [ \"$status\" = 'dead' ]; then "
        f"    exit 2; "
        f"  fi; "
        f"  if [ \"$status\" = 'running' ] && [ -n \"$pid\" ] && [ \"$pid\" -gt 0 ] 2>/dev/null; then "
        f"    if nsenter -t \"$pid\" -n ss {ss_flags} 'sport = :{port_num}' 2>/dev/null | grep -q .; then "
        f"      exit 0; "
        f"    fi; "
        f"  fi; "
        f"  sleep 0.5; "
        f"done; "
        f"exit 1"
    )
    code, _, _ = runner.run(check_cmd, timeout=timeout + 5)
    if code != 0:
        _, logs, _ = runner.run(f"{rt_bin} logs --tail 50 {container_q} 2>/dev/null", timeout=10)
        logs_detail = logs.strip() if logs else "container exited or port not open"
        raise StandaloneProxyError(
            f"Proxy readiness check failed ({rt_bin}, port {port}/{'udp' if is_udp else 'tcp'}): {logs_detail}"
        )


_STOPPED_CONTAINER_STATES = frozenset({"exited", "stopped", "dead", "created", "configured", "initialized"})


def _resolve_compose_runtime(runner: RemoteRunner) -> str:
    try:
        return _detect_compose_runtime(runner)
    except RuntimeError:
        ensure_podman(runner)
        return _detect_compose_runtime(runner)


def _is_binary_explicitly_missing(runner: RemoteRunner, rt_bin: str) -> bool:
    """Return True only when the remote shell explicitly confirms rt_bin is absent."""
    cmd = f"command -v {rt_bin} >/dev/null 2>&1 && echo __HOPLYRA_BIN_PRESENT__ || echo __HOPLYRA_BIN_MISSING__"
    code, out, _ = runner.run(cmd, timeout=10, silent=True)
    if code != 0:
        return False
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return lines == ["__HOPLYRA_BIN_MISSING__"]


def _probe_container_in_engine(
    runner: RemoteRunner,
    container: str,
    rt_bin: str,
    *,
    allow_missing_binary: bool,
) -> str:
    """Return container status string, 'absent', or 'missing_binary' (only if explicitly absent).
    Raises RuntimeError on query/transport failure."""
    container_q = shlex.quote(container)
    code, out, err = runner.run(f"{rt_bin} inspect -f '{{{{.State.Status}}}}' {container_q}", timeout=20)
    if code == 0:
        state = out.strip().lower()
        return state or "unknown"

    detail = (err or out or f"exit {code}").strip()
    ps_code, ps_out, ps_err = runner.run(f"{rt_bin} ps -a --format '{{{{.Names}}}}'", timeout=20)
    if ps_code == 0:
        names = {line.strip() for line in ps_out.splitlines() if line.strip()}
        if container in names:
            raise RuntimeError(f"Container {container} exists in {rt_bin} and inspect failed: {detail}")
        return "absent"

    if allow_missing_binary and _is_binary_explicitly_missing(runner, rt_bin):
        return "missing_binary"

    ps_detail = (ps_err or ps_out or detail).strip()
    raise RuntimeError(f"Failed to verify container {container} status in {rt_bin}: {ps_detail}")


def _ensure_container_stopped_or_absent(
    runner: RemoteRunner,
    container: str,
    runtime: str | None,
) -> None:
    if isinstance(runtime, str) and runtime.strip():
        rt_bin = "docker" if "docker" in runtime.lower() else "podman"
        status = _probe_container_in_engine(runner, container, rt_bin, allow_missing_binary=False)
        if status == "absent" or status in _STOPPED_CONTAINER_STATES:
            return
        raise RuntimeError(f"Container {container} is not stopped in {rt_bin} (status: {status})")

    # Existing record with runtime=NULL: never rely on checking a single auto-detected engine.
    checked_engines = 0
    for rt_bin in ("docker", "podman"):
        status = _probe_container_in_engine(runner, container, rt_bin, allow_missing_binary=True)
        if status == "missing_binary":
            continue
        checked_engines += 1
        if status == "absent" or status in _STOPPED_CONTAINER_STATES:
            continue
        raise RuntimeError(f"Container {container} is not stopped in {rt_bin} (status: {status})")

    if checked_engines == 0:
        raise RuntimeError(
            f"Failed to verify container {container} status: runtime is unknown and neither docker nor podman is available"
        )


def _detect_compose_runtime_for_engine(runner: RemoteRunner, engine_bin: str) -> str:
    if engine_bin == "docker":
        code, _, _ = runner.run("docker compose version >/dev/null 2>&1", timeout=30, silent=True)
        if code == 0:
            return "docker compose"
        raise RuntimeError("Docker Compose unavailable on VPS")

    code, _, _ = runner.run("podman compose version >/dev/null 2>&1", timeout=30, silent=True)
    if code == 0:
        return "podman compose"
    code, _, _ = runner.run("command -v podman-compose >/dev/null 2>&1", timeout=30, silent=True)
    if code == 0:
        return "podman-compose"
    raise RuntimeError("Podman Compose unavailable on VPS")


def _resolve_legacy_runtime_for_start(runner: RemoteRunner, container: str) -> str:
    """For legacy records with runtime=NULL, locate existing container owner across docker & podman
    before binding runtime in DB."""
    owners: list[str] = []
    for rt_bin in ("docker", "podman"):
        status = _probe_container_in_engine(runner, container, rt_bin, allow_missing_binary=True)
        if status in ("missing_binary", "absent"):
            continue
        owners.append(rt_bin)

    if len(owners) > 1:
        raise StandaloneProxyError(
            f"Ambiguous proxy runtime: container {container} exists in multiple engines ({', '.join(owners)})"
        )
    if len(owners) == 1:
        return _detect_compose_runtime_for_engine(runner, owners[0])
    return _resolve_compose_runtime(runner)


def list_standalone_proxies() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM standalone_proxies ORDER BY created_at DESC").fetchall()
        return [row_to_standalone_proxy(r) for r in rows]


def get_standalone_proxy_by_id(proxy_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM standalone_proxies WHERE id = ?", (proxy_id,)).fetchone()
        if not row:
            raise LookupError(f"Proxy not found: {proxy_id}")
        return row_to_standalone_proxy(row)


def deploy_standalone_proxy(
    *,
    server_id: str,
    proxy_type: str,
    name: str | None = None,
    listen_port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    secret: str | None = None,
    sni: str | None = None,
    cipher: str | None = None,
    async_deploy: bool = False,
    get_server_row: Any,
    server_target: Any,
) -> dict[str, Any]:
    if proxy_type not in SUPPORTED_PROXY_TYPES:
        raise StandaloneProxyError(f"Unsupported proxy type: {proxy_type}. Supported: {', '.join(SUPPORTED_PROXY_TYPES)}")

    if listen_port is not None:
        port_candidate = int(listen_port)
        if port_candidate < 1 or port_candidate > 65535:
            raise StandaloneProxyError("listenPort must be between 1 and 65535")

    # Validate credentials / parameters before touching remote server or DB
    user = ""
    pwd = ""
    chosen_cipher = ""
    target_sni = ""
    fake_tls_secret = ""

    if proxy_type in ("socks5", "https"):
        if username is not None and username != "":
            user = username.strip()
            if not user:
                raise StandaloneProxyError("Username cannot be whitespace-only")
        else:
            user = f"user{secrets.token_hex(3)}"

        if password is not None and password != "":
            if not password.strip():
                raise StandaloneProxyError("Password cannot be whitespace-only")
            pwd = password
        else:
            pwd = secrets.token_urlsafe(16)

    elif proxy_type == "shadowsocks":
        chosen_cipher = (cipher or "AEAD_CHACHA20_POLY1305").strip()
        if chosen_cipher not in SS_CIPHER_STANDARD_NAMES:
            raise StandaloneProxyError(f"Unsupported Shadowsocks cipher: {chosen_cipher}")

        if password is not None and password != "":
            if not password.strip():
                raise StandaloneProxyError("Password cannot be whitespace-only")
            pwd = password
        else:
            pwd = secrets.token_urlsafe(20)

    elif proxy_type == "mtproto":
        if secret is not None and secret.strip() != "":
            fake_tls_secret, target_sni = _validate_mtproto_secret(secret)
        else:
            target_sni = _normalize_domain(sni or "cloudflare.com")
            raw_secret = secrets.token_hex(16)
            domain_hex = target_sni.encode("ascii").hex()
            fake_tls_secret = f"ee{raw_secret}{domain_hex}"

    s_row = get_server_row(server_id)
    if not s_row:
        raise StandaloneProxyError("Server not found")

    target = server_target(s_row)
    runner = RemoteRunner(target)
    server_host = s_row["host"]

    proxy_id = new_id()
    project = _compose_project(proxy_id)
    container = _container_name(proxy_id)
    instance_path = _instance_dir(proxy_id, target.is_local)
    proxy_name = (name or f"{proxy_type.upper()}-{server_host}").strip()
    default_p = DEFAULT_PROXY_PORTS.get(proxy_type, 1080)
    explicit_port = int(listen_port) if listen_port is not None else None
    initial_port = explicit_port if explicit_port is not None else 0

    meta: dict[str, Any] = {
        "proxyType": proxy_type,
        "listenPort": initial_port,
        "serverHost": server_host,
    }
    if proxy_type in ("socks5", "https"):
        meta["username"] = user
        meta["password"] = pwd
    elif proxy_type == "shadowsocks":
        meta["cipher"] = chosen_cipher
        meta["password"] = pwd
    elif proxy_type == "mtproto":
        meta["secret"] = fake_tls_secret
        meta["sni"] = target_sni

    deploy_log.clear(proxy_id)
    deploy_log.mark_active(proxy_id)
    deploy_log.publish(proxy_id, f"Deploying {proxy_type.upper()} proxy '{proxy_name}' on {server_host}", stage="init")

    created_at = _now()
    _register_proxy_deploy(proxy_id)
    try:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if explicit_port is not None:
                reserved_ports = _collect_reserved_ports(conn, server_id)
                if explicit_port in reserved_ports:
                    conn.rollback()
                    raise StandaloneProxyError(f"Port {explicit_port} is already in use on server {server_host}")
            conn.execute(
                """INSERT INTO standalone_proxies
                   (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, runtime, client_uri, meta_json, created_at)
                   VALUES (?, ?, ?, ?, ?, 'deploying', ?, ?, NULL, '', ?, ?)""",
                (
                    proxy_id,
                    server_id,
                    proxy_name,
                    proxy_type,
                    initial_port,
                    container,
                    instance_path,
                    json.dumps(meta),
                    created_at,
                ),
            )
            conn.commit()
    except Exception as exc:
        _unregister_proxy_deploy(proxy_id)
        deploy_log.publish(proxy_id, f"Proxy deployment failed: {exc}", level="error", stage="error")
        deploy_log.mark_finished(proxy_id, False)
        raise

    def _execute() -> dict[str, Any]:
        runtime: str | None = None
        compose_started = False
        deploy_ok = False
        try:
            with _proxy_lock_context(proxy_id, blocking=True):
                try:
                    with deploy_log.deploy_context(proxy_id):
                        if explicit_port is not None:
                            port = explicit_port
                            deploy_log.publish(proxy_id, f"Using requested port {port}", stage="port")
                        else:
                            deploy_log.publish(proxy_id, f"Scanning free ports on {server_host} starting from {default_p}", stage="port")
                            port = _reserve_free_proxy_port(
                                runner,
                                proxy_id=proxy_id,
                                server_id=server_id,
                                default_port=default_p,
                                proxy_type=proxy_type,
                                meta=meta,
                            )
                            deploy_log.publish(proxy_id, f"Selected free port {port}", stage="port")
                        meta["listenPort"] = port

                        deploy_log.publish(proxy_id, f"Creating workspace directory {instance_path}", stage="config")
                        mkdir_remote(runner, instance_path)

                        client_uri = ""
                        compose_yaml = ""

                        if proxy_type == "socks5":
                            deploy_log.publish(proxy_id, "Ensuring container image hoplyra-gost:1", stage="image")
                            ensure_image(runner, "hoplyra-gost:1", "gost")
                            host_str = endpoint_host(server_host)
                            client_uri = f"socks5://{urllib_quote(user, safe='')}:{urllib_quote(pwd, safe='')}@{host_str}:{port}"
                            listen_uri = _build_gost_listen_uri("socks5", user, pwd, port)
                            compose_yaml = _render_compose_file(
                                image="hoplyra-gost:1",
                                container_name=container,
                                ports=[f"{port}:{port}/tcp", f"{port}:{port}/udp"],
                                command=["-L", listen_uri],
                            )

                        elif proxy_type == "https":
                            deploy_log.publish(proxy_id, "Ensuring container image hoplyra-gost:1", stage="image")
                            ensure_image(runner, "hoplyra-gost:1", "gost")
                            host_str = endpoint_host(server_host)
                            client_uri = f"https://{urllib_quote(user, safe='')}:{urllib_quote(pwd, safe='')}@{host_str}:{port}"
                            deploy_log.publish(proxy_id, f"Generating TLS certificate for {server_host}", stage="config")
                            cert_pem, key_pem, pin_sha256 = generate_self_signed_cert_bundle(server_host)
                            meta["pinSha256"] = pin_sha256
                            runner.upload_text(f"{instance_path}/cert.pem", cert_pem, 0o644)
                            runner.upload_text(f"{instance_path}/key.pem", key_pem, 0o600)
                            listen_uri = _build_gost_listen_uri(
                                "http+tls",
                                user,
                                pwd,
                                port,
                                query="cert=/etc/gost/cert.pem&key=/etc/gost/key.pem",
                            )
                            compose_yaml = _render_compose_file(
                                image="hoplyra-gost:1",
                                container_name=container,
                                ports=[f"{port}:{port}/tcp"],
                                volumes=[
                                    f"{instance_path}/cert.pem:/etc/gost/cert.pem:ro",
                                    f"{instance_path}/key.pem:/etc/gost/key.pem:ro",
                                ],
                                command=["-L", listen_uri],
                            )

                        elif proxy_type == "shadowsocks":
                            deploy_log.publish(proxy_id, "Ensuring container image hoplyra-gost:1", stage="image")
                            ensure_image(runner, "hoplyra-gost:1", "gost")
                            host_str = endpoint_host(server_host)
                            sip002_cipher = SS_CIPHER_STANDARD_NAMES[chosen_cipher]
                            ss_creds = f"{sip002_cipher}:{pwd}"
                            ss_b64 = base64.urlsafe_b64encode(ss_creds.encode()).decode().rstrip("=")
                            client_uri = f"ss://{ss_b64}@{host_str}:{port}#{urllib_quote(proxy_name, safe='')}"
                            listen_uri = _build_gost_listen_uri("ss", chosen_cipher, pwd, port)
                            compose_yaml = _render_compose_file(
                                image="hoplyra-gost:1",
                                container_name=container,
                                ports=[f"{port}:{port}/tcp", f"{port}:{port}/udp"],
                                command=["-L", listen_uri],
                            )

                        elif proxy_type == "mtproto":
                            deploy_log.publish(proxy_id, "Ensuring container image hoplyra-mtg:2", stage="image")
                            ensure_image(runner, "hoplyra-mtg:2", "mtg")
                            host_str = endpoint_host(server_host)
                            client_uri = f"tg://proxy?server={host_str}&port={port}&secret={fake_tls_secret}"
                            compose_yaml = _render_compose_file(
                                image="hoplyra-mtg:2",
                                container_name=container,
                                ports=[f"{port}:{port}/tcp"],
                                command=["simple-run", f"0.0.0.0:{port}", fake_tls_secret],
                            )

                        deploy_log.publish(proxy_id, "Uploading docker-compose configuration", stage="config")
                        runner.upload_text(f"{instance_path}/docker-compose.yml", compose_yaml)

                        runtime = _resolve_compose_runtime(runner)
                        meta["runtime"] = runtime

                        # Persist runtime and final listen_port/client_uri BEFORE first compose up
                        with connect() as conn:
                            conn.execute(
                                "UPDATE standalone_proxies SET listen_port = ?, runtime = ?, client_uri = ?, meta_json = ? WHERE id = ?",
                                (port, runtime, client_uri, json.dumps(meta), proxy_id),
                            )
                            conn.commit()

                        deploy_log.publish(proxy_id, f"Starting container {container} via {runtime}", stage="container")
                        compose_started = True
                        runtime = podman_compose_up(runner, instance_path, project, runtime=runtime)
                        meta["runtime"] = runtime

                        deploy_log.publish(proxy_id, f"Waiting for port {port} readiness", stage="ready")
                        _wait_proxy_ready(runner, container, port, is_udp=False, runtime=runtime)

                        with connect() as conn:
                            conn.execute(
                                "UPDATE standalone_proxies SET status = 'active', runtime = ?, meta_json = ? WHERE id = ?",
                                (runtime, json.dumps(meta), proxy_id),
                            )
                            conn.commit()
                            row = conn.execute("SELECT * FROM standalone_proxies WHERE id = ?", (proxy_id,)).fetchone()
                            result = row_to_standalone_proxy(row)

                        deploy_log.publish(proxy_id, f"{proxy_type.upper()} proxy active on {server_host}:{port}", level="success", stage="finish")
                        deploy_ok = True
                        return result

                except Exception as exc:
                    if compose_started and runtime:
                        try:
                            deploy_log.publish(proxy_id, "Rolling back failed container stack", level="warn", stage="rollback")
                            podman_compose_down(runner, instance_path, project, strict=True, runtime=runtime)
                        except Exception as down_exc:
                            log.warning("Cleanup compose down failed for %s: %s", proxy_id, down_exc)
                            meta["cleanupError"] = str(down_exc)
                    with connect() as conn:
                        conn.execute(
                            "UPDATE standalone_proxies SET status = 'error', runtime = ?, meta_json = ? WHERE id = ?",
                            (runtime, json.dumps({**meta, "error": str(exc)}), proxy_id),
                        )
                        conn.commit()
                    deploy_log.publish(proxy_id, f"Proxy deployment failed: {exc}", level="error", stage="error")
                    if not async_deploy:
                        if not compose_started and isinstance(exc, StandaloneProxyError):
                            raise
                        raise StandaloneProxyError(f"Failed to deploy proxy: {exc}") from exc
                    return {}
        finally:
            _unregister_proxy_deploy(proxy_id)
            deploy_log.mark_finished(proxy_id, deploy_ok)

    if async_deploy:
        snapshot = get_standalone_proxy_by_id(proxy_id)

        def _thread_runner() -> None:
            try:
                _execute()
            finally:
                _unregister_proxy_thread(threading.current_thread())

        t = threading.Thread(target=_thread_runner, daemon=False, name=f"hoplyra-proxy-{proxy_id[:8]}")
        try:
            _start_tracked_proxy_thread(t)
        except Exception:
            _unregister_proxy_deploy(proxy_id)
            with connect() as conn:
                conn.execute(
                    "UPDATE standalone_proxies SET status = 'error', meta_json = ? WHERE id = ?",
                    (json.dumps({**meta, "error": "Failed to start deployment worker thread"}), proxy_id),
                )
                conn.commit()
            deploy_log.publish(proxy_id, "Failed to start deployment worker thread", level="error", stage="error")
            deploy_log.mark_finished(proxy_id, False)
            raise
        return snapshot

    return _execute()


def stop_standalone_proxy(proxy_id: str, *, get_server_row: Any, server_target: Any) -> dict[str, Any]:
    with _proxy_operation_guard(proxy_id, "stop"):
        prx = get_standalone_proxy_by_id(proxy_id)
        s_row = get_server_row(prx["serverId"])
        runner = RemoteRunner(server_target(s_row))
        project = _compose_project(proxy_id)
        runtime = prx.get("runtime") or prx.get("meta", {}).get("runtime")
        container_name = prx.get("containerName") or _container_name(proxy_id)
        podman_compose_down(runner, prx["instancePath"], project, strict=True, runtime=runtime)
        _ensure_container_stopped_or_absent(runner, container_name, runtime)
        with connect() as conn:
            conn.execute("UPDATE standalone_proxies SET status = 'inactive' WHERE id = ?", (proxy_id,))
            conn.commit()
        return {"status": "inactive"}


def start_standalone_proxy(proxy_id: str, *, get_server_row: Any, server_target: Any) -> dict[str, Any]:
    with _proxy_operation_guard(proxy_id, "start"):
        prx = get_standalone_proxy_by_id(proxy_id)
        if not prx.get("clientUri") or int(prx.get("listenPort") or 0) <= 0:
            raise StandaloneProxyError(
                "Proxy deployment was interrupted before configuration completed; delete and deploy again"
            )
        s_row = get_server_row(prx["serverId"])
        runner = RemoteRunner(server_target(s_row))
        project = _compose_project(proxy_id)
        saved_runtime = prx.get("runtime") or prx.get("meta", {}).get("runtime")
        container_name = prx.get("containerName") or _container_name(proxy_id)
        meta = dict(prx.get("meta") or {})
        if not (isinstance(saved_runtime, str) and saved_runtime.strip()):
            try:
                saved_runtime = _resolve_legacy_runtime_for_start(runner, container_name)
            except Exception as exc:
                if isinstance(exc, StandaloneProxyError):
                    raise
                raise StandaloneProxyError(f"Failed to determine proxy runtime: {exc}") from exc
            meta["runtime"] = saved_runtime
            with connect() as conn:
                conn.execute(
                    "UPDATE standalone_proxies SET runtime = ?, meta_json = ? WHERE id = ?",
                    (saved_runtime, json.dumps(meta), proxy_id),
                )
                conn.commit()

        runtime: str = saved_runtime
        try:
            runtime = podman_compose_up(runner, prx["instancePath"], project, runtime=saved_runtime)
            _wait_proxy_ready(runner, prx["containerName"], prx["listenPort"], is_udp=False, runtime=runtime)
        except Exception as exc:
            try:
                podman_compose_down(runner, prx["instancePath"], project, strict=True, runtime=runtime)
            except Exception as down_exc:
                log.warning("Rollback compose down failed for %s: %s", proxy_id, down_exc)
                meta["cleanupError"] = str(down_exc)
            meta["error"] = str(exc)
            with connect() as conn:
                conn.execute(
                    "UPDATE standalone_proxies SET status = 'error', runtime = ?, meta_json = ? WHERE id = ?",
                    (runtime, json.dumps(meta), proxy_id),
                )
                conn.commit()
            raise StandaloneProxyError(f"Failed to start proxy: {exc}") from exc

        meta["runtime"] = runtime
        meta.pop("error", None)
        meta.pop("cleanupError", None)
        with connect() as conn:
            conn.execute(
                "UPDATE standalone_proxies SET status = 'active', runtime = ?, meta_json = ? WHERE id = ?",
                (runtime, json.dumps(meta), proxy_id),
            )
            conn.commit()
        return {"status": "active"}


def delete_standalone_proxy(proxy_id: str, *, get_server_row: Any, server_target: Any) -> dict[str, Any]:
    with _proxy_operation_guard(proxy_id, "delete"):
        prx = get_standalone_proxy_by_id(proxy_id)
        s_row = get_server_row(prx["serverId"])
        runner = RemoteRunner(server_target(s_row))
        project = _compose_project(proxy_id)
        runtime = prx.get("runtime") or prx.get("meta", {}).get("runtime")
        instance_path = prx.get("instancePath")
        container_name = prx.get("containerName") or _container_name(proxy_id)
        meta = dict(prx.get("meta") or {})
        has_compose = bool(prx.get("clientUri")) and int(prx.get("listenPort") or 0) > 0

        try:
            if instance_path and has_compose:
                podman_compose_down(runner, instance_path, project, strict=True, runtime=runtime)
            if has_compose or (isinstance(runtime, str) and runtime.strip()):
                _ensure_container_stopped_or_absent(runner, container_name, runtime)
            if instance_path:
                code, out, err = runner.run(f"rm -rf {shlex.quote(instance_path)}")
                if code != 0:
                    detail = (err or out or f"exit {code}").strip()
                    raise RuntimeError(f"Failed to remove directory {instance_path}: {detail}")
        except Exception as exc:
            meta["error"] = str(exc)
            with connect() as conn:
                conn.execute(
                    "UPDATE standalone_proxies SET status = 'error', meta_json = ? WHERE id = ?",
                    (json.dumps(meta), proxy_id),
                )
                conn.commit()
            raise StandaloneProxyError(f"Failed to delete proxy: {exc}") from exc

        with connect() as conn:
            conn.execute("DELETE FROM standalone_proxies WHERE id = ?", (proxy_id,))
            conn.commit()
        return {"status": "deleted"}


def recover_stuck_standalone_proxies(
    proxy_ids: set[str] | None = None,
    *,
    get_server_row: Any,
    server_target: Any,
) -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM standalone_proxies WHERE status = 'deploying'").fetchall()

    outcomes: dict[str, str] = {}
    for row in rows:
        proxy_id = row["id"]
        if proxy_ids is not None and proxy_id not in proxy_ids:
            continue
        if is_proxy_deploy_active(proxy_id):
            log.info("Skip recover for active standalone proxy deploy=%s", proxy_id)
            continue

        try:
            with _proxy_lock_context(proxy_id, blocking=False):
                if is_proxy_deploy_active(proxy_id):
                    continue
                with connect() as conn:
                    fresh = conn.execute("SELECT * FROM standalone_proxies WHERE id = ?", (proxy_id,)).fetchone()
                if not fresh or fresh["status"] != "deploying":
                    continue

                meta = json.loads(fresh["meta_json"] or "{}")
                runtime = fresh["runtime"] or meta.get("runtime")
                container_name = fresh["container_name"] or _container_name(proxy_id)
                listen_port = int(fresh["listen_port"] or 0)
                client_uri = fresh["client_uri"] or ""

                recovered_active = False
                if isinstance(runtime, str) and runtime.strip() and listen_port > 0 and client_uri:
                    try:
                        s_row = get_server_row(fresh["server_id"])
                        if s_row:
                            runner = RemoteRunner(server_target(s_row))
                            rt_bin = "docker" if "docker" in runtime.lower() else "podman"
                            c_state = _probe_container_in_engine(
                                runner, container_name, rt_bin, allow_missing_binary=False
                            )
                            if c_state == "running":
                                _wait_proxy_ready(
                                    runner,
                                    container_name,
                                    listen_port,
                                    is_udp=False,
                                    runtime=runtime,
                                    timeout=2,
                                )
                                recovered_active = True
                    except Exception as exc:
                        log.warning(
                            "Recovered stuck deploying proxy %s container check failed: %s",
                            proxy_id,
                            exc,
                        )

                if recovered_active:
                    meta["runtime"] = runtime
                    meta.pop("error", None)
                    meta.pop("cleanupError", None)
                    with connect() as conn:
                        conn.execute(
                            "UPDATE standalone_proxies SET status = 'active', runtime = ?, meta_json = ? WHERE id = ? AND status = 'deploying'",
                            (runtime, json.dumps(meta), proxy_id),
                        )
                        conn.commit()
                    outcomes[proxy_id] = "active"
                    log.info("Recovered running standalone proxy %s -> active", proxy_id)
                else:
                    if listen_port > 0 and client_uri:
                        meta["error"] = (
                            "Proxy deployment interrupted by service restart. Retry start or delete and redeploy."
                        )
                    else:
                        meta["error"] = (
                            "Proxy deployment interrupted by service restart before completion. Delete and redeploy."
                        )
                    with connect() as conn:
                        conn.execute(
                            "UPDATE standalone_proxies SET status = 'error', meta_json = ? WHERE id = ? AND status = 'deploying'",
                            (json.dumps(meta), proxy_id),
                        )
                        conn.commit()
                    outcomes[proxy_id] = "error"
                    log.warning("Recovered stuck deploying standalone proxy %s -> error", proxy_id)
        except StandaloneProxyConflictError:
            continue
    return outcomes


def schedule_recover_stuck_standalone_proxies(*, get_server_row: Any, server_target: Any) -> threading.Thread | None:
    with connect() as conn:
        proxy_ids = {
            row["id"] for row in conn.execute("SELECT id FROM standalone_proxies WHERE status = 'deploying'").fetchall()
        }
    if not proxy_ids:
        return None

    def _worker() -> None:
        try:
            recover_stuck_standalone_proxies(
                proxy_ids,
                get_server_row=get_server_row,
                server_target=server_target,
            )
        finally:
            _unregister_proxy_thread(threading.current_thread())

    t = threading.Thread(
        target=_worker,
        daemon=True,
        name="hoplyra-proxy-recover",
    )
    _start_tracked_proxy_thread(t)
    return t
