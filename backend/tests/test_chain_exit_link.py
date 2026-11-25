from unittest.mock import MagicMock, patch
import json

import pytest

from hoplyra.chains.deployer import ChainDeployer, _link_net
from hoplyra.chains.planner import ExitEndpoint, build_plan
from hoplyra.xray_runtime import compose_xray_service


def _probe_out(rules="[]", routes="[]", iface="M_IFACE_ABSENT", res="M_RES_ABSENT"):
    return f"M_RULE\n{rules}\nM_ROUTE\n{routes}\nM_IFACE\n{iface}\nM_RES\n{res}\nM_END"


def runner():
    remote = MagicMock()
    remote.target.host = "203.0.113.1"
    remote.target.is_local = False

    def run(cmd, timeout=60):
        if "M_END" in cmd:
            return 0, _probe_out(), ""
        return 0, "", ""
    remote.run.side_effect = run
    return remote


@pytest.mark.parametrize("protocol,gateway", [("awg", "10.9.1.1"), ("wg", "10.77.0.1")])
@pytest.mark.parametrize("entry_if", [None, "tun0", "awg0", "wg0"])
def test_link_policy_is_scoped_and_masqueraded(protocol, gateway, entry_if):
    remote = runner()
    endpoint = ExitEndpoint("203.0.113.2", 55424, link_protocol=protocol,
                            link_client_conf="[Interface]\nPrivateKey = test\nDNS = 1.1.1.1\n[Peer]\nAllowedIPs = 0.0.0.0/0\n")
    deployer = ChainDeployer("/tmp")
    with patch.object(deployer, "_awg_up_with_fallback", return_value=(True, None)), \
         patch("hoplyra.chains.deployer._ensure_compose_images"), \
         patch("hoplyra.chains.deployer.podman_compose_up"):
        deployer._deploy_exit_link(remote, "/chain/entry", "config", endpoint, entry_if)
    conf = next(c.args[1] for c in remote.upload_text.call_args_list if c.args[0].endswith('.conf'))
    _, table, mark = _link_net("config")
    assert "Table = off" in conf
    assert "DNS =" not in conf
    assert "-o %i -j MASQUERADE" in conf
    assert f"dev %i table {table}" in conf
    assert f"priority {table}" in conf
    if entry_if:
        assert f"ip rule add iif {entry_if}" in conf
        assert "ip route add 128.0.0.0/1" in conf
        assert "fwmark" not in conf
    else:
        assert f"ip rule add fwmark {mark:#x}" in conf
        assert "ip route add default" in conf
    # PostUp must not touch foreign routing tables (High: shared table 100 wipe).
    assert "table 100" not in conf.replace(f"table {table}", "")


def test_two_chains_get_distinct_link_resources():
    t1, m1 = _link_net("aaaa1111-2222-3333-4444-5555")[1:]
    t2, m2 = _link_net("bbbb1111-2222-3333-4444-6666")[1:]
    assert (t1, m1) != (t2, m2)


def test_occupied_table_is_skipped_and_foreign_routes_untouched():
    remote = runner()
    _, base_table, _ = _link_net("config")

    def run(cmd, timeout=60):
        if "M_END" in cmd:
            if f"table {base_table}" in cmd:
                # Foreign VPN already owns the first candidate table.
                return 0, _probe_out(rules=json.dumps([{"priority": 32000, "table": base_table}]),
                                     routes=json.dumps([{"dst": "10.0.0.0/8", "dev": "eth9"}])), ""
            return 0, _probe_out(), ""
        return 0, "", ""
    remote.run.side_effect = run

    endpoint = ExitEndpoint("203.0.113.2", 55424, link_protocol="awg",
                            link_client_conf="[Interface]\nPrivateKey = test\n[Peer]\nAllowedIPs = 0.0.0.0/0\n")
    deployer = ChainDeployer("/tmp")
    with patch.object(deployer, "_awg_up_with_fallback", return_value=(True, None)), \
         patch("hoplyra.chains.deployer._ensure_compose_images"), \
         patch("hoplyra.chains.deployer.podman_compose_up"):
        deployer._deploy_exit_link(remote, "/chain/entry", "config", endpoint, None)
    conf = next(c.args[1] for c in remote.upload_text.call_args_list if c.args[0].endswith('.conf'))
    assert f"table {base_table}" not in conf  # foreign table untouched
    assert "flush" not in conf  # exact deletions only, never a table flush
    assert endpoint.link_table != base_table  # next candidate chosen
    assert f"table {endpoint.link_table}" in conf


def test_unreserved_live_rule_lookalike_is_foreign():
    # A foreign rule mentioning iif tun0 without our reservation file must NOT
    # be adopted as ours (regression: substring match used to claim it).
    remote = runner()
    _, base_table, _ = _link_net("config")

    def run(cmd, timeout=60):
        if "M_END" in cmd:
            if f"table {base_table}" in cmd:
                # Foreign rule with our priority/lookup but no reservation file.
                return 0, _probe_out(rules=json.dumps(
                    [{"priority": 100, "iif": "tun0", "table": base_table}])), ""
            return 0, _probe_out(), ""
        return 0, "", ""
    remote.run.side_effect = run

    endpoint = ExitEndpoint("203.0.113.2", 55424, link_protocol="awg",
                            link_client_conf="[Interface]\nPrivateKey = test\n[Peer]\nAllowedIPs = 0.0.0.0/0\n")
    deployer = ChainDeployer("/tmp")
    with patch.object(deployer, "_awg_up_with_fallback", return_value=(True, None)), \
         patch("hoplyra.chains.deployer._ensure_compose_images"), \
         patch("hoplyra.chains.deployer.podman_compose_up"):
        deployer._deploy_exit_link(remote, "/chain/entry", "config", endpoint, "tun0")
    assert endpoint.link_table != base_table


def test_probe_failure_aborts_without_touching_network():
    remote = runner()
    remote.run.side_effect = lambda cmd, timeout=60: (1, "", "boom")
    endpoint = ExitEndpoint("203.0.113.2", 55424, link_protocol="awg",
                            link_client_conf="[Interface]\nPrivateKey = test\n[Peer]\nAllowedIPs = 0.0.0.0/0\n")
    deployer = ChainDeployer("/tmp")
    with pytest.raises(RuntimeError, match="сетевые ресурсы"):
        deployer._deploy_exit_link(remote, "/chain/entry", "config", endpoint, None)
    remote.upload_text.assert_not_called()


def test_internal_probe_command_failure_with_sentinel_aborts():
    # M_END is present, but an inner ip command failed: must NOT read as free.
    remote = runner()
    remote.run.side_effect = lambda cmd, timeout=60: (
        (0, "M_RULE\nM_FAIL_RULE\nM_ROUTE\n[]\nM_IFACE\nM_IFACE_ABSENT\nM_RES\nM_RES_ABSENT\nM_END", "")
        if "M_END" in cmd else (0, "", ""))
    endpoint = ExitEndpoint("203.0.113.2", 55424, link_protocol="awg",
                            link_client_conf="[Interface]\nPrivateKey = test\n[Peer]\nAllowedIPs = 0.0.0.0/0\n")
    deployer = ChainDeployer("/tmp")
    with pytest.raises(RuntimeError, match="сетевые ресурсы"):
        deployer._deploy_exit_link(remote, "/chain/entry", "config", endpoint, None)
    remote.upload_text.assert_not_called()


def test_fwmark_mask_intersection_skips_candidate():
    remote = runner()
    _, base_table, base_mark = _link_net("config")

    def run(cmd, timeout=60):
        if "M_END" in cmd:
            if f"table {base_table}" in cmd:
                # Foreign rule matches our exact candidate mark -> conflict.
                return 0, _probe_out(rules=json.dumps(
                    [{"priority": 90, "fwmark": hex(base_mark), "fwmask": "0xffffffff", "table": "main"}])), ""
            # Disjoint mask on other candidates: no intersection -> free.
            return 0, _probe_out(rules=json.dumps(
                [{"priority": 90, "fwmark": "0xdead0000", "fwmask": "0xffff0000", "table": "main"}])), ""
        return 0, "", ""
    remote.run.side_effect = run

    endpoint = ExitEndpoint("203.0.113.2", 55424, link_protocol="awg",
                            link_client_conf="[Interface]\nPrivateKey = test\n[Peer]\nAllowedIPs = 0.0.0.0/0\n")
    deployer = ChainDeployer("/tmp")
    with patch.object(deployer, "_awg_up_with_fallback", return_value=(True, None)), \
         patch("hoplyra.chains.deployer._ensure_compose_images"), \
         patch("hoplyra.chains.deployer.podman_compose_up"):
        deployer._deploy_exit_link(remote, "/chain/entry", "config", endpoint, None)
    assert endpoint.link_table != base_table
    assert endpoint.link_mark != base_mark


def test_own_reservation_reused_even_with_live_interface():
    remote = runner()
    _, base_table, _ = _link_net("config")
    own_iface = f"awglnk{_link_net('config')[0]}"

    def run(cmd, timeout=60):
        if "M_END" in cmd:
            if f"table {base_table}" in cmd:
                # Own reservation + only our own expected entries -> reuse.
                return 0, _probe_out(
                    rules=json.dumps([{"priority": base_table, "fwmark": "0x1", "table": base_table}]),
                    routes=json.dumps([{"dst": "default", "dev": own_iface}]),
                    iface='[{"ifname":"' + own_iface + '"}]',
                    res="config"), ""
            return 0, _probe_out(), ""
        return 0, "", ""
    remote.run.side_effect = run

    endpoint = ExitEndpoint("203.0.113.2", 55424, link_protocol="awg",
                            link_client_conf="[Interface]\nPrivateKey = test\n[Peer]\nAllowedIPs = 0.0.0.0/0\n")
    deployer = ChainDeployer("/tmp")
    with patch.object(deployer, "_awg_up_with_fallback", return_value=(True, None)), \
         patch("hoplyra.chains.deployer._ensure_compose_images"), \
         patch("hoplyra.chains.deployer.podman_compose_up"):
        deployer._deploy_exit_link(remote, "/chain/entry", "config", endpoint, None)
    assert endpoint.link_table == base_table  # reservation wins over live iface


def test_own_reservation_with_foreign_route_aborts_before_network():
    remote = runner()
    _, base_table, _ = _link_net("config")

    def run(cmd, timeout=60):
        if "M_END" in cmd:
            if f"table {base_table}" in cmd:
                # Admin route with the same prefix via another interface.
                return 0, _probe_out(
                    routes=json.dumps([{"dst": "default", "dev": "wg99"}]),
                    res="config"), ""
            return 0, _probe_out(), ""
        return 0, "", ""
    remote.run.side_effect = run

    endpoint = ExitEndpoint("203.0.113.2", 55424, link_protocol="awg",
                            link_client_conf="[Interface]\nPrivateKey = test\n[Peer]\nAllowedIPs = 0.0.0.0/0\n")
    deployer = ChainDeployer("/tmp")
    with pytest.raises(RuntimeError, match="чужие маршруты"):
        deployer._deploy_exit_link(remote, "/chain/entry", "config", endpoint, None)
    remote.upload_text.assert_not_called()  # network untouched


def test_probe_uses_numeric_table_ids():
    # A table named in rt_tables must not slip through: probe with -N.
    remote = runner()
    endpoint = ExitEndpoint("203.0.113.2", 55424, link_protocol="awg",
                            link_client_conf="[Interface]\nPrivateKey = test\n[Peer]\nAllowedIPs = 0.0.0.0/0\n")
    deployer = ChainDeployer("/tmp")
    with patch.object(deployer, "_awg_up_with_fallback", return_value=(True, None)), \
         patch("hoplyra.chains.deployer._ensure_compose_images"), \
         patch("hoplyra.chains.deployer.podman_compose_up"):
        deployer._deploy_exit_link(remote, "/chain/entry", "config", endpoint, None)
    probe_cmds = [c.args[0] for c in remote.run.call_args_list if "M_END" in c.args[0]]
    assert any("ip -N -j -4 rule show" in c for c in probe_cmds)


def test_marked_outbound_and_xray_capability():
    endpoint = ExitEndpoint("exit", 55424, link_client_conf="peer", link_protocol="awg")
    cfg = ChainDeployer("/tmp")._xray_chain_config(endpoint, False, False)
    outbound = cfg["outbounds"][0]
    assert outbound == {"tag": "exit", "protocol": "freedom",
                        "settings": {"domainStrategy": "UseIPv4"},
                        "streamSettings": {"sockopt": {"mark": 102}}}
    service = compose_xray_service("test", "/chain", net_admin=True)
    assert "network_mode: host" in service
    assert "cap_add: [NET_ADMIN]" in service
    plain = compose_xray_service("test", "/chain")
    assert "cap_add" not in plain


@pytest.mark.parametrize("protocol", ["awg", "wg"])
def test_exit_has_no_socks_front(protocol):
    plan = build_plan([{"id": "a", "protocol": "xray", "serverId": "a"},
                       {"id": "b", "protocol": protocol, "serverId": "b"}],
                      {"a": "203.0.113.1", "b": "203.0.113.2"})
    deployer = ChainDeployer("/tmp")
    with patch.object(deployer, "_deploy_nat_exit", return_value=(ExitEndpoint("exit", 55424), "peer")), \
         patch.object(deployer, "_deploy_xray_socks_exit") as socks:
        endpoint = deployer._deploy_exit_server(runner(), "config", plan, [plan.exit], None)
    assert endpoint.link_client_conf == "peer"
    assert endpoint.link_protocol == protocol
    socks.assert_not_called()


def test_hysteria_bridge_marks_exit():
    deployer = ChainDeployer("/tmp")
    endpoint = ExitEndpoint("exit", 55424, link_client_conf="peer", link_protocol="awg")
    plan = build_plan([{"id": "a", "protocol": "hysteria2", "serverId": "a"},
                       {"id": "b", "protocol": "awg", "serverId": "b"}], {"a": "entry", "b": "exit"})
    with patch("hoplyra.protocols.find_hysteria2_port", return_value=443), \
         patch("hoplyra.protocols.hysteria2_bundle", return_value=("config", "service", "client", {}, ("cert", "key"))):
        _, services, files, _ = deployer._hysteria2_entry_bundle(runner(), "/chain", "config", plan.entry, endpoint, False)
    bridge = json.loads(next(content for name, content, _ in files if name == "hy2-bridge.json"))
    assert bridge["outbounds"][0]["streamSettings"]["sockopt"]["mark"] == 102
    assert any("NET_ADMIN" in service for service in services)


@pytest.mark.parametrize("protocol", ["awg", "wg"])
def test_nat_exit_returns_matching_peer_config(protocol):
    remote = runner()
    deployer = ChainDeployer("/tmp")
    plan = build_plan([{"id": "a", "protocol": "xray", "serverId": "a"},
                       {"id": "b", "protocol": protocol, "serverId": "b"}], {"a": "entry", "b": "exit"})
    with patch.object(deployer, "_awg_up_with_fallback", return_value=(True, None)), \
         patch("hoplyra.chains.deployer._ensure_compose_images"), \
         patch("hoplyra.chains.deployer.podman_compose_up"):
        endpoint, conf = deployer._deploy_nat_exit(remote, "/chain", "config", plan.exit)
    assert endpoint.host == "exit"
    assert endpoint.port == (55424 if protocol == "awg" else 51821)
    assert f"Endpoint = exit:{endpoint.port}" in conf
    assert "PersistentKeepalive = 15" in conf
    assert "PrivateKey =" in conf


def test_awg_entry_exit_subnets_do_not_overlap():
    remote = runner()
    deployer = ChainDeployer("/tmp")
    plan = build_plan([{"id": "a", "protocol": "awg", "serverId": "a"},
                       {"id": "b", "protocol": "awg", "serverId": "b"}], {"a": "entry", "b": "exit"})
    with patch.object(deployer, "_awg_up_with_fallback", return_value=(True, None)):
        endpoint = deployer._deploy_exit_server(remote, "config", plan, [plan.exit], None)
    assert "10.79.1.2/32" in endpoint.link_client_conf
    server_conf = next(call.args[1] for call in remote.upload_text.call_args_list if call.args[0].endswith("awg0.conf"))
    assert "10.79.1.1/24" in server_conf
    assert "10.79.1.2/32" in server_conf
