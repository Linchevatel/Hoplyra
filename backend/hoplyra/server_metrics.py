from __future__ import annotations

import shlex
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from hoplyra import db
from hoplyra.remote import RemoteRunner, ServerTarget, measure_tcp_latency, test_ssh_auth
from hoplyra.secrets import decrypt_auth_secret

CONTROL_SERVER_ID = "__control__"

METRICS_PY = r"""
import subprocess
import time
from pathlib import Path

def read_cpu():
    parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:11]
    nums = [int(x) for x in parts]
    idle = nums[3] + nums[4]
    return sum(nums), idle

def read_net():
    rx = tx = 0
    skip = ("lo", "docker", "veth", "br-", "virbr", "podman")
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        parts = line.split()
        if not parts:
            continue
        iface = parts[0].rstrip(":")
        if iface == "lo" or any(iface.startswith(p) for p in skip):
            continue
        rx += int(parts[1])
        tx += int(parts[9])
    return rx, tx

def container_count():
    for cmd in (("podman", "ps", "-q"), ("docker", "ps", "-q")):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if r.returncode == 0:
                return len([ln for ln in r.stdout.splitlines() if ln.strip()])
        except Exception:
            pass
    return 0

def meminfo():
    data = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        data[key.strip()] = int(val.strip().split()[0])
    total = data.get("MemTotal")
    avail = data.get("MemAvailable", data.get("MemFree"))
    return total, avail

def diskinfo():
    line = subprocess.run(
        ["df", "-B1", "/"],
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip().splitlines()[-1].split()
    return int(line[1]), int(line[2]), int(line[3])

uptime_s = int(float(Path("/proc/uptime").read_text().split()[0]))
load1, load2, load3, *_ = (Path("/proc/loadavg").read_text().split() + ["0", "0", "0"])[:3]
mt, ma = meminfo()
dt, du, da = diskinfo()
nr1, nt1 = read_net()
t1, i1 = read_cpu()
time.sleep(1)
nr2, nt2 = read_net()
t2, i2 = read_cpu()
d = max(t2 - t1, 1)
cpu = max(0.0, min(100.0, (1 - (i2 - i1) / d) * 100))
net_rx = max(0, nr2 - nr1)
net_tx = max(0, nt2 - nt1)
cnt = container_count()
print(
    f"uptime_s={uptime_s}\n"
    f"load1={load1}\nload2={load2}\nload3={load3}\n"
    f"mem_total_kb={mt}\nmem_avail_kb={ma}\n"
    f"disk_total_b={dt}\ndisk_used_b={du}\ndisk_avail_b={da}\n"
    f"cpu_percent={cpu:.1f}\n"
    f"net_rx_bps={net_rx}\nnet_tx_bps={net_tx}\n"
    f"containers={cnt}\n",
    end="",
)
""".strip()


def _metrics_python_cmd() -> str:
    return f"python3 -c {shlex.quote(METRICS_PY)}"


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


def _run_metrics_script(*, timeout: int) -> dict[str, str]:
    if sys.platform == "win32":
        return _run_metrics_script_windows(timeout=timeout)
    proc = subprocess.run(
        ["python3", "-c", METRICS_PY],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or proc.stdout or "metrics command failed").strip()[:500]
        raise RuntimeError(detail)
    return _parse_kv_output(proc.stdout)


def _container_count_local() -> int:
    for cmd in (
        ("podman", "ps", "-q"),
        ("docker", "ps", "-q"),
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if proc.returncode == 0:
                return len([ln for ln in proc.stdout.splitlines() if ln.strip()])
        except OSError:
            pass
    return 0


def _run_metrics_script_windows(*, timeout: int) -> dict[str, str]:
    del timeout
    import psutil

    disk_root = "C:\\" if sys.platform == "win32" else "/"
    net1 = psutil.net_io_counters()
    cpu_sample = psutil.cpu_percent(interval=None)
    time.sleep(1)
    cpu = psutil.cpu_percent(interval=None)
    if cpu_sample is not None and cpu == 0.0:
        cpu = cpu_sample
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(disk_root)
    net2 = psutil.net_io_counters()
    uptime_s = max(0, int(time.time() - psutil.boot_time()))
    load1 = load5 = load15 = None
    try:
        load1, load5, load15 = psutil.getloadavg()
    except (AttributeError, OSError):
        pass

    nr1 = net1.bytes_recv if net1 else 0
    nt1 = net1.bytes_sent if net1 else 0
    nr2 = net2.bytes_recv if net2 else 0
    nt2 = net2.bytes_sent if net2 else 0

    kv = {
        "uptime_s": str(uptime_s),
        "load1": "" if load1 is None else str(load1),
        "load2": "" if load5 is None else str(load5),
        "load3": "" if load15 is None else str(load15),
        "mem_total_kb": str(mem.total // 1024),
        "mem_avail_kb": str(mem.available // 1024),
        "disk_total_b": str(disk.total),
        "disk_used_b": str(disk.used),
        "disk_avail_b": str(disk.free),
        "cpu_percent": f"{cpu:.1f}",
        "net_rx_bps": str(max(0, nr2 - nr1)),
        "net_tx_bps": str(max(0, nt2 - nt1)),
        "containers": str(_container_count_local()),
    }
    return kv


def _metrics_payload_from_script(*, timeout: int) -> dict[str, Any]:
    collected_at = datetime.now(timezone.utc).isoformat()
    kv = _run_metrics_script(timeout=timeout)
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
        base.update(_metrics_payload_from_script(timeout=10))
        base["online"] = True
        return base
    except Exception as exc:
        base["error"] = str(exc)[:500]
        return base


def collect_server_metrics(runner: RemoteRunner) -> dict[str, Any]:
    collected_at = datetime.now(timezone.utc).isoformat()
    code, out, err = runner.run(_metrics_python_cmd(), timeout=20)
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
        secret = decrypt_auth_secret(row["auth_secret"])
        if not secret:
            return {
                "serverId": row["id"],
                "name": row["name"],
                "host": row["host"],
                "latencyMs": row["latency_ms"],
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
                "error": "Server credentials missing",
            }
        target = RemoteRunner(
            ServerTarget(
                host=row["host"],
                port=row["port"],
                username=row["username"],
                auth_type=row["auth_type"] or "password",
                auth_secret=secret,
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
    with ThreadPoolExecutor(max_workers=min(3, len(rows))) as pool:
        futures = {pool.submit(one, row): row["id"] for row in rows}
        for fut in as_completed(futures):
            results.append(fut.result())
    order = {row["id"]: idx for idx, row in enumerate(rows)}
    results.sort(key=lambda item: order.get(item["serverId"], 999))
    return results
