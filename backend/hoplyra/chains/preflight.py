from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hoplyra.host_setup import ensure_host_ready
from hoplyra.remote import RemoteRunner, ServerTarget, measure_tcp_latency, test_ssh_auth


@dataclass
class ServerCheckResult:
    server_id: str
    name: str
    host: str
    ok: bool
    error: str | None = None
    latency_ms: int | None = None


class ChainPreflightError(Exception):
    def __init__(self, failures: list[ServerCheckResult]) -> None:
        self.failures = failures
        parts = [f"{f.name} ({f.host}): {f.error}" for f in failures if not f.ok]
        super().__init__("Серверы недоступны — деплой цепи не начат. " + "; ".join(parts))


def _server_label(row: dict[str, Any] | Any, server_id: str) -> str:
    if isinstance(row, dict):
        return str(row.get("name") or server_id[:8])
    return str(row["name"] if row["name"] else server_id[:8])


def _server_host(row: dict[str, Any] | Any) -> str:
    if isinstance(row, dict):
        return str(row["host"])
    return str(row["host"])


def check_server_reachable(
    target: ServerTarget,
    *,
    name: str,
    server_id: str,
) -> ServerCheckResult:
    if target.is_local:
        return ServerCheckResult(server_id, name, target.host, True, latency_ms=0)

    latency = measure_tcp_latency(target.host, target.port, timeout=10.0)
    if latency is None:
        return ServerCheckResult(
            server_id,
            name,
            target.host,
            False,
            f"порт SSH {target.port} недоступен",
        )

    ok, err = test_ssh_auth(target)
    if not ok:
        return ServerCheckResult(
            server_id,
            name,
            target.host,
            False,
            err or "SSH недоступен",
            latency,
        )
    return ServerCheckResult(server_id, name, target.host, True, latency_ms=latency)


def verify_chain_servers(
    servers: dict[str, dict[str, Any]],
    targets: dict[str, ServerTarget],
    *,
    prepare_hosts: bool = True,
) -> dict[str, RemoteRunner]:
    results = [
        check_server_reachable(
            targets[sid],
            name=_server_label(servers[sid], sid),
            server_id=sid,
        )
        for sid in servers
    ]
    failures = [r for r in results if not r.ok]
    if failures:
        raise ChainPreflightError(failures)

    runners: dict[str, RemoteRunner] = {}
    prepare_errors: list[ServerCheckResult] = []
    for sid in servers:
        runner = RemoteRunner(targets[sid])
        if prepare_hosts:
            try:
                ensure_host_ready(runner)
            except Exception as exc:
                prepare_errors.append(
                    ServerCheckResult(
                        sid,
                        _server_label(servers[sid], sid),
                        _server_host(servers[sid]),
                        False,
                        str(exc)[:500],
                    )
                )
                continue
        runners[sid] = runner

    if prepare_errors:
        raise ChainPreflightError(prepare_errors)
    return runners


def recheck_runners_online(runners: dict[str, RemoteRunner]) -> None:
    failures: list[ServerCheckResult] = []
    for sid, runner in runners.items():
        host = runner.target.host
        if runner.target.is_local:
            continue
        latency = measure_tcp_latency(host, runner.target.port, timeout=10.0)
        if latency is None:
            failures.append(
                ServerCheckResult(sid, host, host, False, f"порт SSH {runner.target.port} недоступен")
            )
            continue
        code, out, err = runner.run("echo HOPLYRA_OK", timeout=20)
        if code != 0 or "HOPLYRA_OK" not in out:
            detail = (err or out or "SSH недоступен").strip()[:500]
            failures.append(ServerCheckResult(sid, host, host, False, detail, latency))
    if failures:
        raise ChainPreflightError(failures)
