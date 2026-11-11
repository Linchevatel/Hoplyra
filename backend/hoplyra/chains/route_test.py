from __future__ import annotations

import shlex
from datetime import datetime, timezone
from typing import Any, Callable

from hoplyra.chains.planner import validate_chain
from hoplyra.chains.preflight import check_server_reachable
from hoplyra.remote import RemoteRunner, ServerTarget, measure_tcp_latency


def _protocol_processing_ms(protocol: str) -> int:
    if protocol == "tor":
        return 80
    if protocol == "xray":
        return 25
    if protocol == "openvpn":
        return 15
    return 8


def _measure_vps_to_vps(from_runner: RemoteRunner, to_host: str) -> int | None:
    if from_runner.target.host == to_host:
        return 0

    host_q = shlex.quote(to_host)
    ping_cmd = (
        f"ping -c 1 -W 3 {host_q} 2>/dev/null "
        r"| grep -oE 'time=[0-9.]+' | head -1 | cut -d= -f2"
    )
    code, out, _ = from_runner.run(ping_cmd, timeout=15)
    if code == 0 and out.strip():
        try:
            return max(1, int(float(out.strip())))
        except ValueError:
            pass

    py = (
        "import socket,time\n"
        f"host={to_host!r}\n"
        "t=time.perf_counter()\n"
        "socket.create_connection((host,22),5).close()\n"
        "print(int((time.perf_counter()-t)*1000))"
    )
    code, out, _ = from_runner.run(f"python3 -c {shlex.quote(py)}", timeout=20)
    if code == 0 and out.strip().isdigit():
        return max(1, int(out.strip()))

    return measure_tcp_latency(to_host, 22, timeout=10.0)


def test_chain_route(
    hops: list[dict[str, Any]],
    *,
    get_server_row: Callable[[str], Any],
    server_target: Callable[[Any], ServerTarget],
) -> dict[str, Any]:
    validate_chain(hops)

    results: list[dict[str, Any]] = []
    runners: dict[str, RemoteRunner] = {}
    prev_server_id: str | None = None

    for hop in hops:
        hop_id = hop["id"]
        server_id = hop["serverId"]
        protocol = hop["protocol"]
        row = get_server_row(server_id)
        target = server_target(row)
        name = str(row["name"] if row["name"] else server_id[:8])

        check = check_server_reachable(target, name=name, server_id=server_id)
        if not check.ok:
            results.append(
                {
                    "hopId": hop_id,
                    "serverId": server_id,
                    "protocol": protocol,
                    "reachable": False,
                    "latencyMs": 0,
                    "error": check.error or "Сервер недоступен",
                }
            )
            prev_server_id = server_id
            continue

        runner = RemoteRunner(target)
        runners[server_id] = runner

        if prev_server_id is None:
            segment_ms = check.latency_ms or 0
        elif prev_server_id == server_id:
            segment_ms = 0
        else:
            prev_runner = runners.get(prev_server_id)
            segment_ms = None
            if prev_runner:
                segment_ms = _measure_vps_to_vps(prev_runner, target.host)
            if segment_ms is None:
                segment_ms = check.latency_ms or 0

        latency_ms = segment_ms + _protocol_processing_ms(protocol)
        results.append(
            {
                "hopId": hop_id,
                "serverId": server_id,
                "protocol": protocol,
                "reachable": True,
                "latencyMs": latency_ms,
            }
        )
        prev_server_id = server_id

    ok = all(r["reachable"] for r in results)
    total = sum(r["latencyMs"] for r in results if r["reachable"])

    return {
        "ok": ok,
        "totalLatencyMs": total,
        "hops": results,
        "testedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
