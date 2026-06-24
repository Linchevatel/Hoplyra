from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class HopSpec:
    id: str
    protocol: str
    server_id: str
    server_host: str
    index: int
    transport: str | None = None
    xray_bypass: bool = False


@dataclass
class ExitEndpoint:
    host: str
    port: int
    auth_user: str | None = None
    auth_pass: str | None = None
    via_tor: bool = False
    direct: bool = False
    tor_dynamic_exit: bool = False


@dataclass
class ChainPlan:
    hops: list[HopSpec]
    entry: HopSpec
    exit: HopSpec
    servers_order: list[str]


def validate_chain(hops: list[dict[str, Any]]) -> None:
    if len(hops) < 2:
        raise ValueError("Цепь должна содержать минимум 2 звена")
    if hops[0]["protocol"] == "tor":
        raise ValueError("Tor не может быть первым звеном")
    for i in range(len(hops) - 1):
        a, b = hops[i], hops[i + 1]
        if a["serverId"] == b["serverId"]:
            if a["protocol"] != "tor" and b["protocol"] != "tor":
                raise ValueError(
                    f"Звенья {i + 1} и {i + 2} на одном VPS — разрешено только если одно из них Tor"
                )


def build_plan(hops: list[dict[str, Any]], server_hosts: dict[str, str]) -> ChainPlan:
    validate_chain(hops)
    specs = [
        HopSpec(
            id=h["id"],
            protocol=h["protocol"],
            server_id=h["serverId"],
            server_host=server_hosts[h["serverId"]],
            index=i,
            transport=h.get("transport"),
            xray_bypass=bool(h.get("xrayBypass")),
        )
        for i, h in enumerate(hops)
    ]
    entry_id = specs[0].server_id
    exit_id = specs[-1].server_id
    seen: set[str] = set()
    servers_order: list[str] = []
    for hop in reversed(specs):
        if hop.server_id not in seen:
            seen.add(hop.server_id)
            servers_order.append(hop.server_id)
    if entry_id == exit_id and len(servers_order) > 1:
        servers_order.append(entry_id)
    return ChainPlan(hops=specs, entry=specs[0], exit=specs[-1], servers_order=servers_order)


def hops_on_server(plan: ChainPlan, server_id: str) -> list[HopSpec]:
    return [h for h in plan.hops if h.server_id == server_id]


def normalize_stored_hops(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    has_tor = any(h.get("protocol") == "tor" for h in hops)
    normalized: list[dict[str, Any]] = []
    for h in hops:
        hop = dict(h)
        if hop.get("protocol") == "openvpn":
            t = (hop.get("transport") or "udp").lower()
            if has_tor:
                hop["transport"] = "tcp"
            else:
                hop["transport"] = t if t in ("udp", "tcp") else "udp"
        normalized.append(hop)
    return normalized
