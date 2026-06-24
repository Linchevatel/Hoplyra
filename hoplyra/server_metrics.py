from __future__ import annotations

import shlex
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from hoplyra import db
from hoplyra.remote import RemoteRunner, ServerTarget, measure_tcp_latency, test_ssh_auth

CONTROL_SERVER_ID = "__control__"

METRICS_SCRIPT = """
read_cpu() {
  read _ u n s id iw ir sf st _gn < /proc/stat
  echo $((u + n + s + id + iw + ir + sf + st)) $((id + iw))
}
read_net() {
  awk 'NR>2 {
    gsub(":", "", $1)
    if ($1 != "lo" && $1 !~ /^(docker|veth|br-|virbr|podman)/) { rx += $2; tx += $10 }
  } END { print rx + 0, tx + 0 }' /proc/net/dev
}
uptime_s=$(cut -d. -f1 /proc/uptime)
read l1 l2 l3 _rest < /proc/loadavg || true
mt=$(awk '/^MemTotal/ {print $2; exit}' /proc/meminfo)
ma=$(awk '/^MemAvailable/ {print $2; exit}' /proc/meminfo)
[ -n "$ma" ] || ma=$(awk '/^MemFree/ {print $2; exit}' /proc/meminfo)
disk_line=$(df -B1 / 2>/dev/null | tail -1)
dt=$(echo "$disk_line" | awk '{print $2}')
du=$(echo "$disk_line" | awk '{print $3}')
da=$(echo "$disk_line" | awk '{print $4}')
read nr1 nt1 < <(read_net)
read t1 i1 < <(read_cpu)
sleep 1
read t2 i2 < <(read_cpu)
read nr2 nt2 < <(read_net)
d=$((t2 - t1))
di=$((i2 - i1))
cpu=$(awk -v d="$d" -v i="$di" 'BEGIN {
  if (d > 0) { v = (1 - i / d) * 100 } else { v = 0 }
  if (v < 0) v = 0
  if (v > 100) v = 100
  printf "%.1f", v
}')
net_rx=$(awk -v a="$nr1" -v b="$nr2" 'BEGIN { d = b - a; if (d < 0) d = 0; printf "%.0f", d }')
net_tx=$(awk -v a="$nt1" -v b="$nt2" 'BEGIN { d = b - a; if (d < 0) d = 0; printf "%.0f", d }')
cnt=$( (podman ps -q 2>/dev/null || docker ps -q 2>/dev/null) | wc -l | tr -d ' ')
printf 'uptime_s=%s\nload1=%s\nload2=%s\nload3=%s\nmem_total_kb=%s\nmem_avail_kb=%s\ndisk_total_b=%s\ndisk_used_b=%s\ndisk_avail_b=%s\ncpu_percent=%s\nnet_rx_bps=%s\nnet_tx_bps=%s\ncontainers=%s\n' \
  "$uptime_s" "$l1" "$l2" "$l3" "$mt" "$ma" "$dt" "$du" "$da" "$cpu" "$net_rx" "$net_tx" "$cnt"
""".strip()

FALLBACK_SCRIPT = """
read_cpu() {
  read _ u n s id iw ir sf st _gn < /proc/stat
  echo $((u + n + s + id + iw + ir + sf + st)) $((id + iw))
}
read_net() {
  awk 'NR>2 {
    gsub(":", "", $1)
    if ($1 != "lo" && $1 !~ /^(docker|veth|br-|virbr|podman)/) { rx += $2; tx += $10 }
  } END { print rx + 0, tx + 0 }' /proc/net/dev
}
mt=$(awk '/^MemTotal/ {print $2; exit}' /proc/meminfo)
ma=$(awk '/^MemAvailable/ {print $2; exit}' /proc/meminfo)
[ -n "$ma" ] || ma=$(awk '/^MemFree/ {print $2; exit}' /proc/meminfo)
disk_line=$(df -B1 / 2>/dev/null | tail -1)
dt=$(echo "$disk_line" | awk '{print $2}')
du=$(echo "$disk_line" | awk '{print $3}')
da=$(echo "$disk_line" | awk '{print $4}')
uptime_s=$(cut -d. -f1 /proc/uptime)
read l1 l2 l3 _rest < /proc/loadavg || true
read nr1 nt1 < <(read_net)
read t1 i1 < <(read_cpu)
sleep 1
read t2 i2 < <(read_cpu)
read nr2 nt2 < <(read_net)
d=$((t2 - t1))
di=$((i2 - i1))
cpu=$(awk -v d="$d" -v i="$di" 'BEGIN {
  if (d > 0) { v = (1 - i / d) * 100 } else { v = 0 }
  if (v < 0) v = 0
  if (v > 100) v = 100
  printf "%.1f", v
}')
net_rx=$(awk -v a="$nr1" -v b="$nr2" 'BEGIN { d = b - a; if (d < 0) d = 0; printf "%.0f", d }')
net_tx=$(awk -v a="$nt1" -v b="$nt2" 'BEGIN { d = b - a; if (d < 0) d = 0; printf "%.0f", d }')
cnt=$( (podman ps -q 2>/dev/null || docker ps -q 2>/dev/null) | wc -l | tr -d ' ')
printf 'uptime_s=%s\nload1=%s\nload2=%s\nload3=%s\nmem_total_kb=%s\nmem_avail_kb=%s\ndisk_total_b=%s\ndisk_used_b=%s\ndisk_avail_b=%s\ncpu_percent=%s\nnet_rx_bps=%s\nnet_tx_bps=%s\ncontainers=%s\n' \
  "$uptime_s" "$l1" "$l2" "$l3" "$mt" "$ma" "$dt" "$du" "$da" "$cpu" "$net_rx" "$net_tx" "$cnt"
""".strip()


def _remote_shell(script: str) -> str:
    return f"bash -lc {shlex.quote(script)}"


def _parse_kv_output(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _metrics_from_kv(kv: dict[str, str]) -> dict[str, Any]:
    mem_total_kb = _int(kv.get("mem_total_kb"))
    mem_avail_kb = _int(kv.get("mem_avail_kb"))
    mem_used_kb = None
    if mem_total_kb is not None and mem_avail_kb is not None:
        mem_used_kb = max(mem_total_kb - mem_avail_kb, 0)

    disk_total = _int(kv.get("disk_total_b"))
    disk_used = _int(kv.get("disk_used_b"))
    disk_avail = _int(kv.get("disk_avail_b"))

    return {
        "uptimeSeconds": _int(kv.get("uptime_s")),
        "load1": _float(kv.get("load1")),
        "load5": _float(kv.get("load2")),
        "load15": _float(kv.get("load3")),
        "cpuPercent": _float(kv.get("cpu_percent")),
        "memoryTotalBytes": mem_total_kb * 1024 if mem_total_kb is not None else None,
        "memoryUsedBytes": mem_used_kb * 1024 if mem_used_kb is not None else None,
        "memoryAvailableBytes": mem_avail_kb * 1024 if mem_avail_kb is not None else None,
        "diskTotalBytes": disk_total,
        "diskUsedBytes": disk_used,
        "diskAvailableBytes": disk_avail,
        "containerCount": _int(kv.get("containers")),
        "networkRxBps": _int(kv.get("net_rx_bps")),
        "networkTxBps": _int(kv.get("net_tx_bps")),
    }


def _run_metrics_script(script: str, *, timeout: int) -> dict[str, str]:
    proc = subprocess.run(
        ["bash", "-lc", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or proc.stdout or "metrics command failed").strip()[:500]
        raise RuntimeError(detail)
    return _parse_kv_output(proc.stdout)


def _metrics_payload_from_script(script: str, *, timeout: int) -> dict[str, Any]:
    collected_at = datetime.now(timezone.utc).isoformat()
    kv = _run_metrics_script(script, timeout=timeout)
    metrics = _metrics_from_kv(kv)
    if metrics["memoryTotalBytes"] is None and metrics["cpuPercent"] is None:
        raise RuntimeError("metrics output incomplete")
    return {
        "online": True,
        "collectedAt": collected_at,
        "error": None,
        **metrics,
    }


def collect_local_metrics() -> dict[str, Any]:
    host = socket.gethostname() or "localhost"
    base: dict[str, Any] = {
        "serverId": CONTROL_SERVER_ID,
        "name": host,
        "host": host,
        "latencyMs": None,
        "isControl": True,
        "online": False,
        "collectedAt": datetime.now(timezone.utc).isoformat(),
        "uptimeSeconds": None,
        "load1": None,
        "load5": None,
        "load15": None,
        "cpuPercent": None,
        "memoryTotalBytes": None,
        "memoryUsedBytes": None,
        "memoryAvailableBytes": None,
        "diskTotalBytes": None,
        "diskUsedBytes": None,
        "diskAvailableBytes": None,
        "containerCount": None,
        "networkRxBps": None,
        "networkTxBps": None,
        "error": None,
    }
    try:
        base.update(_metrics_payload_from_script(METRICS_SCRIPT, timeout=10))
        base["online"] = True
        return base
    except Exception as exc:
        base["error"] = str(exc)[:500]
        return base


def collect_server_metrics(runner: RemoteRunner) -> dict[str, Any]:
    collected_at = datetime.now(timezone.utc).isoformat()
    code, out, err = runner.run(_remote_shell(METRICS_SCRIPT), timeout=20)
    if code != 0 or not out.strip():
        code, out, err = runner.run(_remote_shell(FALLBACK_SCRIPT), timeout=15)
        if code != 0 or not out.strip():
            raise RuntimeError((err or out or "metrics command failed").strip()[:500])

    kv = _parse_kv_output(out)
    metrics = _metrics_from_kv(kv)
    if metrics["memoryTotalBytes"] is None and metrics["cpuPercent"] is None:
        raise RuntimeError("metrics output incomplete")

    return {
        "online": True,
        "collectedAt": collected_at,
        "error": None,
        **metrics,
    }


def metrics_for_target(
    server_id: str,
    name: str,
    host: str,
    runner: RemoteRunner,
    *,
    latency_ms: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    fresh_latency = measure_tcp_latency(host, runner.target.port)
    display_latency = fresh_latency if fresh_latency is not None else latency_ms
    if fresh_latency is not None and fresh_latency != latency_ms:
        try:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE servers SET latency_ms=? WHERE id=?",
                    (fresh_latency, server_id),
                )
        except Exception:
            pass

    base: dict[str, Any] = {
        "serverId": server_id,
        "name": name,
        "host": host,
        "latencyMs": display_latency,
        "online": False,
        "collectedAt": datetime.now(timezone.utc).isoformat(),
        "uptimeSeconds": None,
        "load1": None,
        "load5": None,
        "load15": None,
        "cpuPercent": None,
        "memoryTotalBytes": None,
        "memoryUsedBytes": None,
        "memoryAvailableBytes": None,
        "diskTotalBytes": None,
        "diskUsedBytes": None,
        "diskAvailableBytes": None,
        "containerCount": None,
        "networkRxBps": None,
        "networkTxBps": None,
        "error": None,
    }

    if status == "connecting":
        base["error"] = "VPS is still preparing"
        return base

    ssh_ok, ssh_err = test_ssh_auth(runner.target)
    if not ssh_ok:
        base["error"] = ssh_err or "SSH unavailable"
        return base

    base["online"] = True
    try:
        metrics = collect_server_metrics(runner)
        base.update(metrics)
        base["serverId"] = server_id
        base["name"] = name
        base["host"] = host
        base["latencyMs"] = fresh_latency if fresh_latency is not None else latency_ms
        return base
    except Exception as exc:
        base["error"] = str(exc)[:500]
        return base


def collect_metrics_batch(rows: list[Any]) -> list[dict[str, Any]]:
    if not rows:
        return []

    def one(row: Any) -> dict[str, Any]:
        target = RemoteRunner(
            ServerTarget(
                host=row["host"],
                port=row["port"],
                username=row["username"],
                auth_type="password",
                auth_secret=row["auth_secret"],
            )
        )
        return metrics_for_target(
            row["id"],
            row["name"],
            row["host"],
            target,
            latency_ms=row["latency_ms"],
            status=row["status"],
        )

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(6, len(rows))) as pool:
        futures = {pool.submit(one, row): row["id"] for row in rows}
        for fut in as_completed(futures):
            results.append(fut.result())
    order = {row["id"]: idx for idx, row in enumerate(rows)}
    results.sort(key=lambda item: order.get(item["serverId"], 999))
    return results
