from __future__ import annotations

import logging
import shlex

from hoplyra.awg_runtime import awg_quick_down, chain_tag_from_config_id, discover_awg_confs
from hoplyra.remote import RemoteRunner
from hoplyra.wg_runtime import HOST_GATEWAY_IPTABLES_CLEANUP, HOST_WG_TEARDOWN

log = logging.getLogger("hoplyra")

_HOST_ROUTING_CLEANUP = (
    "ip rule del iif tun0 table 100 priority 100 2>/dev/null || true; "
    "ip rule del from 10.8.0.0/24 table 100 priority 100 2>/dev/null || true; "
    "ip route flush table 100 2>/dev/null || true; "
    "ip link del wg0 2>/dev/null || true; "
    "ip link del awg0 2>/dev/null || true"
)

_WG_LINK_IPTABLES_CLEANUP = (
    "iptables -D FORWARD -i tun0 -o wg0 -j ACCEPT 2>/dev/null || true; "
    "iptables -D FORWARD -i wg0 -o tun0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true; "
    "iptables -D FORWARD -i wg0 -j ACCEPT 2>/dev/null || true; "
    "WAN=$(ip -4 route show default 2>/dev/null | awk '/default/ {print $5; exit}'); "
    '[ -n "$WAN" ] && iptables -D FORWARD -i "$WAN" -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true'
)

_CHAIN_ROOTS = ("/opt/hoplyra/chains", "/opt/chainvault/chains")
_INSTANCE_ROOTS = ("/opt/hoplyra/instances", "/opt/chainvault/instances")


def _wg_link_cleanup_cmd(config_id: str) -> str:
    roots = " ".join(shlex.quote(f"{root}/{config_id}") for root in _CHAIN_ROOTS)
    return (
        f"for root in {roots}; do "
        f'  [ -d "$root" ] || continue; '
        f'  find "$root" -name wg-link.conf 2>/dev/null | while read -r conf; do '
        f'    wg-quick down "$conf" 2>/dev/null || true; '
        f'    d=$(dirname "$conf"); '
        f'    (cd "$d" && (podman compose -p wg-link down 2>/dev/null || '
        f'     podman-compose -p wg-link down 2>/dev/null || true)); '
        f"  done; "
        f"done"
    )


def purge_host_artifacts(runner: RemoteRunner, config_id: str, *, chain: bool) -> None:
    tag = chain_tag_from_config_id(config_id)
    for conf in discover_awg_confs(runner, config_id):
        awg_quick_down(runner, conf)

    if chain:
        runner.run(_wg_link_cleanup_cmd(config_id), timeout=120)

    runner.run(HOST_WG_TEARDOWN, timeout=60)
    runner.run(_HOST_ROUTING_CLEANUP, timeout=60)
    runner.run(_WG_LINK_IPTABLES_CLEANUP, timeout=60)
    runner.run(f"bash -s << 'EOF'\n{HOST_GATEWAY_IPTABLES_CLEANUP}\nEOF", timeout=120)

    runner.run(
        f"podman ps -a --format '{{{{.Names}}}}' 2>/dev/null | grep -E 'cv-.+-{tag}' | "
        f"xargs -r podman rm -f 2>/dev/null || true",
        timeout=300,
    )

    roots = [
        f"/opt/hoplyra/chains/{config_id}",
        f"/opt/hoplyra/instances/{config_id}",
        f"/opt/chainvault/chains/{config_id}",
        f"/opt/chainvault/instances/{config_id}",
    ]
    if chain:
        for suffix in ("entry", "exit", "relay", "link"):
            roots.append(f"/opt/hoplyra/chains/{config_id}/*-{suffix}")
            roots.append(f"/opt/chainvault/chains/{config_id}/*-{suffix}")
    for root in roots:
        if "*" in root:
            runner.run(
                f"for d in {root}; do [ -d \"$d\" ] && rm -rf \"$d\"; done 2>/dev/null || true",
                timeout=120,
            )
        else:
            runner.run(f"rm -rf {shlex.quote(root)}", timeout=120)


def verify_config_purged(
    runner: RemoteRunner,
    config_id: str,
    *,
    chain: bool,
    instance_path: str | None = None,
) -> list[str]:
    issues: list[str] = []
    tag = chain_tag_from_config_id(config_id)

    code, out, _ = runner.run(
        f"podman ps -a --format '{{{{.Names}}}}' 2>/dev/null || true",
        timeout=60,
    )
    for line in out.splitlines():
        name = line.strip()
        if name.startswith("cv-") and tag in name:
            issues.append(f"container {name} still present")

    paths: list[str] = []
    if chain:
        paths.extend(f"{root}/{config_id}" for root in _CHAIN_ROOTS)
    else:
        if instance_path:
            paths.append(instance_path)
        paths.extend(f"{root}/{config_id}" for root in _INSTANCE_ROOTS)

    for path in paths:
        code, _, _ = runner.run(f"test -e {shlex.quote(path)}", timeout=30)
        if code == 0:
            issues.append(f"path {path} still exists")

    return issues
