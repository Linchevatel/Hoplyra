from __future__ import annotations

from itertools import product
from typing import Iterable

from hoplyra.chains.planner import ChainPlan, HopSpec, validate_chain

PROTOCOLS = ("awg", "wg", "openvpn", "xray", "tor")
ENTRY_PROTOCOLS = ("awg", "wg", "openvpn", "xray")
EXIT_PROTOCOLS = ("awg", "wg", "openvpn", "xray", "tor")
TRANSPORT_PROTOCOLS = ("tor",)
RELAY_PROTOCOLS = ("tor", "xray")
MAX_HOPS = 4


def chain_ends_with_tor(plan: ChainPlan) -> bool:
    return plan.exit.protocol == "tor"


def tor_is_transport(hop: HopSpec, plan: ChainPlan) -> bool:
    return hop.protocol == "tor" and hop.index < len(plan.hops) - 1


def is_same_server_pair_allowed(a_protocol: str, b_protocol: str, same_server: bool) -> bool:
    if not same_server:
        return True
    return a_protocol == "tor" or b_protocol == "tor"


def is_valid_hop_sequence(protocols: tuple[str, ...]) -> bool:
    if not (2 <= len(protocols) <= MAX_HOPS):
        return False
    if protocols[0] == "tor":
        return False
    for i in range(len(protocols) - 1):
        if not is_same_server_pair_allowed(protocols[i], protocols[i + 1], False):
            return False
    return True


def enumerate_protocol_sequences(length: int) -> Iterable[tuple[str, ...]]:
    if length < 2 or length > MAX_HOPS:
        return
    for seq in product(PROTOCOLS, repeat=length):
        if seq[0] == "tor":
            continue
        yield seq


def enumerate_valid_chains(
    server_ids: list[str],
    *,
    same_server: bool = False,
) -> Iterable[list[dict[str, str]]]:
    if len(server_ids) < 2:
        return
    n = len(server_ids)

    for length in range(2, MAX_HOPS + 1):
        for protocols in enumerate_protocol_sequences(length):
            if same_server and length > 1:
                                                                                      
                ok = all(
                    is_same_server_pair_allowed(protocols[i], protocols[i + 1], True)
                    for i in range(length - 1)
                )
                if not ok:
                    continue
                hops = [
                    {"id": f"h{i}", "protocol": protocols[i], "serverId": server_ids[0]}
                    for i in range(length)
                ]
            else:
                                                                   
                hops = [
                    {
                        "id": f"h{i}",
                        "protocol": protocols[i],
                        "serverId": server_ids[i % n],
                    }
                    for i in range(length)
                ]
                invalid = False
                for i in range(length - 1):
                    if hops[i]["serverId"] == hops[i + 1]["serverId"]:
                        if not is_same_server_pair_allowed(
                            protocols[i], protocols[i + 1], True
                        ):
                            invalid = True
                            break
                if invalid:
                    continue

            try:
                validate_chain(hops)
            except ValueError:
                continue
            yield hops


def hop_role(hop: HopSpec, plan: ChainPlan) -> str:
    if hop.index == 0:
        return "entry"
    if hop.index == len(plan.hops) - 1:
        return "exit"
    if hop.protocol == "tor":
        return "transport"
    return "relay"


def chain_has_tor(plan: ChainPlan) -> bool:
    return any(h.protocol == "tor" for h in plan.hops)


def hops_share_server(plan: ChainPlan) -> bool:
    return plan.entry.server_id == plan.exit.server_id


def needs_wg_link(plan: ChainPlan) -> bool:
    if plan.entry.server_id == plan.exit.server_id:
        return False
    if chain_has_tor(plan):
        return False
    if plan.entry.protocol != "openvpn":
        return False
    return plan.exit.protocol in ("awg", "wg")


def entry_needs_gateway(plan: ChainPlan, downstream: bool, local_tor: bool) -> bool:
    entry = plan.entry.protocol
    if entry == "xray":
        return False
    if needs_wg_link(plan):
        return False
    if local_tor:
        return True
    return downstream and entry in ("awg", "wg", "openvpn")


def relay_uses_xray(hop: HopSpec) -> bool:
    return hop.protocol not in RELAY_PROTOCOLS


def chain_uses_ovpn_client_tunnel(plan: ChainPlan) -> bool:
    return False


def exit_exposes_socks(protocol: str) -> bool:
    return protocol in ("xray", "tor")
