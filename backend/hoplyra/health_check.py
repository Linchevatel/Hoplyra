"""Read-only, config-scoped runtime health probes."""
from __future__ import annotations

import asyncio
import json
import logging
import shlex
import time
from datetime import datetime, timezone

from hoplyra.db import connect
from hoplyra.remote import RemoteRunner

log = logging.getLogger("hoplyra")


def check_runtime(cfg, runner):
    meta = json.loads(cfg.get("meta_json") or "{}")
    protocol = cfg["protocol"]
    if meta.get("chain"):
        return "degraded", "Chain runtime health probe unavailable"
    quote = shlex.quote
    if protocol in ("awg", "wg"):
        # Naming convention is deterministic (awg_<id8>/wg_<id8>); use it as
        # fallback for configs deployed before interface metadata existed.
        interface = meta.get("interface") or f"{'awg' if protocol == 'awg' else 'wg'}_{cfg['id'][:8]}"
        tool = "awg" if protocol == "awg" else "wg"
        prefix = ""
        # WG runs inside its own network namespace/container.
        if protocol == "wg":
            container = cfg.get("container_name") or f"cv-wg-{cfg['id'][:8]}"
            prefix = f"podman exec {quote(container)} "
        code, out, _ = runner.run(
            f"{prefix}{tool} show {quote(interface)} listen-port", timeout=20, retries=1)
        if code or not out.strip().isdigit():
            return "down", "VPN interface unavailable"
        port = out.strip()
        code, out, _ = runner.run(
            f"ss -H -lun 'sport = :{quote(port)}' | grep -q .", timeout=20, retries=1)
        if code:
            return "degraded", "VPN listen port is not bound"
        code, out, _ = runner.run(
            f"{prefix}{tool} show {quote(interface)} latest-handshakes", timeout=20, retries=1)
        if code:
            return "alive", "Interface up, port listening"
        timestamps = [int(line.split()[-1]) for line in out.splitlines() if line.split() and line.split()[-1].isdigit()]
        fresh = [s for s in timestamps if s and 0 <= time.time() - s < 180]
        if fresh:
            return "alive", "Recent handshake"
        if any(timestamps):
            return "alive", "Interface up, port listening (clients idle)"
        return "alive", "Interface up, port listening (no clients yet)"
    if protocol not in ("xray", "openvpn", "tor"):
        return "degraded", "Unsupported health probe"
    container = cfg.get("container_name") or f"cv-{protocol}-{cfg['id'][:8]}"
    code, out, _ = runner.run("podman ps --format '{{.Names}}'", timeout=20, retries=1)
    if code or container not in out.splitlines():
        return "down", "VPN container is not running"
    port = meta.get("listenPort") or (9050 if protocol == "tor" else None)
    if not port:
        return "degraded", "Listen port metadata unavailable"
    code, out, _ = runner.run("ss -H -lntu", timeout=20, retries=1)
    if code:
        return "down", "Cannot inspect listening ports"
    if any(len(parts := line.split()) > 4 and parts[4].rsplit(":", 1)[-1] == str(port) for line in out.splitlines()):
        return "alive", "Container running and port listening"
    return "down", "VPN port is not listening"


def refresh_config(config_id, get_server_row, server_target, *, cached=True):
    with connect() as conn:
        row = conn.execute("SELECT * FROM configs WHERE id=?", (config_id,)).fetchone()
    if row is None:
        raise LookupError("Config not found")
    cfg = dict(row)
    if cfg["status"] != "active":
        raise ValueError("Health checks require an active config")
    meta = json.loads(cfg["meta_json"] or "{}")
    if cached and meta.get("liveCheckedAt"):
        try:
            age = time.time() - datetime.fromisoformat(meta["liveCheckedAt"]).timestamp()
            if 0 <= age < 30 and meta.get("liveState") in ("alive", "degraded", "down"):
                return {"state": meta["liveState"], "reason": meta.get("liveStateReason", ""), "checkedAt": meta["liveCheckedAt"]}
        except (ValueError, TypeError):
            pass
    try:
        state, reason = check_runtime(cfg, RemoteRunner(server_target(get_server_row(cfg["server_id"]))))
    except Exception as exc:
        # Do not log remote output or exception messages (may contain credentials).
        state, reason = "down", f"SSH health check failed ({type(exc).__name__})"
        log.warning("Config %s health check failed: %s", config_id, type(exc).__name__)
    checked = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        current = conn.execute("SELECT status, meta_json FROM configs WHERE id=?", (config_id,)).fetchone()
        if current and current["status"] == "active":
            meta = json.loads(current["meta_json"] or "{}")
            meta.update(liveState=state, liveStateReason=reason, liveCheckedAt=checked)
            conn.execute("UPDATE configs SET meta_json=? WHERE id=? AND status='active'", (json.dumps(meta), config_id))
    return {"state": state, "reason": reason, "checkedAt": checked}


async def health_loop(get_server_row, server_target):
    while True:
        try:
            with connect() as conn:
                ids = [r["id"] for r in conn.execute("SELECT id FROM configs WHERE status='active'")]
            for config_id in ids:
                try:
                    await asyncio.to_thread(refresh_config, config_id, get_server_row, server_target)
                except (LookupError, ValueError):
                    pass  # Deleted or stopped while waiting.
        except Exception as exc:
            log.warning("Health sweep failed: %s", type(exc).__name__)
        await asyncio.sleep(60)
