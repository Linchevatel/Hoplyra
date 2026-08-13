from __future__ import annotations

import json
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from hoplyra.chains.planner import ChainPlan, ExitEndpoint, HopSpec, hops_on_server
from hoplyra.chains.resolver import (
    chain_has_tor,
    entry_needs_gateway,
    needs_wg_link,
)
from hoplyra.chains.preflight import recheck_runners_online
from hoplyra.deploy_cancel import check_deploy_cancel
from hoplyra.remote import RemoteRunner, mkdir_remote, nat_postdown, nat_postup, podman_compose_up, wan_wg_forward_down_cmd, wan_wg_forward_up_cmd
from hoplyra.wg_keys import generate_wg_keypair
from hoplyra.awg_params import build_awg_client_conf, build_awg_server_conf, generate_awg_params, generate_awg2_params
from hoplyra.awg_runtime import awg_quick_up, ensure_awg_on_host
from hoplyra.amnezia_export import build_amnezia_awg_vpn_uri
from hoplyra.wg_keys import generate_wg_psk
from hoplyra.openvpn_runtime import (
    OPENVPN_IMAGE,
    build_client_ovpn,
    compose_service,
    ensure_openvpn_image,
    ensure_openvpn_port_free,
    provision_openvpn_instance,
    server_conf_content,
)
from hoplyra.wg_runtime import HOST_GATEWAY_IPTABLES_CLEANUP, compose_wg_service, ensure_wg_image
from hoplyra.xray_bypass import (
    build_bypass_client_config,
    build_reality_vless_inbound,
    build_vless_reality_uri,
    bypass_meta_from_keys,
    generate_reality_keypair,
    generate_short_id,
    new_vless_uuid,
)
from hoplyra.xray_runtime import compose_xray_service, ensure_xray_image, tls_cert_paths
from hoplyra.tor_runtime import compose_tor_service, ensure_tor_image
from hoplyra.gateway_runtime import GATEWAY_MOUNT, compose_gateway_service, ensure_gateway_image
from hoplyra.hosts import safe_path_suffix

CHAIN_ROOT = "/opt/hoplyra/chains"
XRAY_ENTRY_PORT = 12345
XRAY_EXIT_PORT = 10808


class SequentialHopStatus:
    def __init__(self, hops: list[HopSpec], on_statuses: Any) -> None:
        self._ordered = sorted(hops, key=lambda h: h.index)
        self._on_statuses = on_statuses
        self._done: set[str] = set()
        self._cursor = 0

    def start(self) -> None:
        self._emit()

    def mark_done(self, hop_id: str) -> None:
        self._done.add(hop_id)
        while self._cursor < len(self._ordered):
            hop = self._ordered[self._cursor]
            if hop.id not in self._done:
                break
            self._cursor += 1
        self._emit()

    def _emit(self) -> None:
        statuses: list[str] = []
        for i, hop in enumerate(self._ordered):
            if i < self._cursor:
                statuses.append("done")
            elif i == self._cursor:
                statuses.append("done" if hop.id in self._done else "deploying")
            else:
                statuses.append("waiting")
        self._on_statuses(statuses)
XRAY_SOCKS_PORT = 1080
TOR_SOCKS_PORT = 9050


def _ovpn_transport(hop: HopSpec, *, plan: ChainPlan | None = None) -> str:
    t = (hop.transport or "udp").lower()
    if plan and chain_has_tor(plan) and hop.protocol == "openvpn":
        return "tcp"
    return t if t in ("udp", "tcp") else "udp"


@dataclass
class ChainDeployResult:
    client_config: str
    instance_path: str
    container_name: str
    meta: dict[str, Any]
    bypass_meta: dict[str, Any] | None = None


def _chain_dir(config_id: str, local: bool, data_dir: str) -> str:
    if local:
        return f"{data_dir}/chains/{config_id}"
    return f"{CHAIN_ROOT}/{config_id}"


def _chain_tag(config_id: str) -> str:
    tail = config_id.rsplit("-", 1)[-1]
    return tail[:8] if len(tail) >= 8 else config_id.replace("-", "")[:8]


def _host_path_suffix(runner: RemoteRunner, prefix: str) -> str:
    return f"/{prefix}-{safe_path_suffix(runner.target.host)}"


def _compose_project(config_id: str) -> str:
    return f"cv-chain-{_chain_tag(config_id)}"


def _compose_project_at(config_id: str, workdir: str) -> str:
    tag = _chain_tag(config_id)
    leaf = workdir.rstrip("/").split("/")[-1]
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", leaf)
    return f"cv-chain-{tag}-{safe}"


def _ensure_compose_images(runner: RemoteRunner, compose: str) -> None:
    if "hoplyra-wg:1" in compose:
        ensure_wg_image(runner)
    if "hoplyra-xray:1" in compose:
        ensure_xray_image(runner)
    if "hoplyra-tor:1" in compose:
        ensure_tor_image(runner)
    if "hoplyra-gateway:1" in compose:
        ensure_gateway_image(runner)
    if OPENVPN_IMAGE in compose:
        ensure_openvpn_image(runner)


class ChainDeployer:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir

    def deploy(
        self,
        runners: dict[str, RemoteRunner],
        plan: ChainPlan,
        config_id: str,
        on_hop_status: Any | None = None,
    ) -> ChainDeployResult:
        downstream: ExitEndpoint | None = None
        client_config = ""
        entry_path = ""
        entry_container = f"cv-chain-{_chain_tag(config_id)}"
        bypass_meta: dict[str, Any] = {}

        colocated_entry_exit = plan.entry.server_id == plan.exit.server_id
        single_vps_chain = colocated_entry_exit and len(plan.servers_order) == 1
        hop_status = SequentialHopStatus(plan.hops, on_hop_status) if on_hop_status else None
        if hop_status:
            hop_status.start()

        entry_awg_prefetch = (
            not single_vps_chain
            and plan.entry.protocol == "awg"
            and plan.entry.server_id != plan.exit.server_id
        )
        if entry_awg_prefetch:
            entry_runner = runners[plan.entry.server_id]
            client_config, entry_path, hop_bypass_meta = self._deploy_entry_awg_only(
                entry_runner, config_id, plan
            )
            bypass_meta.update(hop_bypass_meta)
            if hop_status:
                hop_status.mark_done(plan.entry.id)

        for idx, server_id in enumerate(plan.servers_order):
            check_deploy_cancel(config_id)
            recheck_runners_online(runners)
            server_hops = hops_on_server(plan, server_id)
            runner = runners[server_id]
            is_last = idx == len(plan.servers_order) - 1

            if single_vps_chain:
                client_config, entry_path, hop_bypass_meta = self._deploy_single_server_chain(
                    runner, config_id, plan, server_hops
                )
                bypass_meta.update(hop_bypass_meta)
                if hop_status:
                    for hop in sorted(server_hops, key=lambda h: h.index):
                        hop_status.mark_done(hop.id)
            elif is_last and server_id == plan.entry.server_id:
                if needs_wg_link(plan):
                    self._deploy_wg_link(
                        runners[plan.entry.server_id],
                        runners[plan.exit.server_id],
                        config_id,
                        plan,
                    )
                if entry_awg_prefetch:
                    _, entry_path, hop_bypass_meta = self._deploy_entry_compose(
                        runner,
                        config_id,
                        plan,
                        server_hops,
                        downstream,
                        entry_path=entry_path,
                        client_conf=client_config,
                    )
                else:
                    client_config, entry_path, hop_bypass_meta = self._deploy_entry_server(
                        runner, config_id, plan, server_hops, downstream
                    )
                bypass_meta.update(hop_bypass_meta)
                if hop_status:
                    for hop in sorted(server_hops, key=lambda h: h.index):
                        if entry_awg_prefetch and hop.id == plan.entry.id:
                            continue
                        hop_status.mark_done(hop.id)
            elif server_id == plan.exit.server_id and idx == 0:
                downstream = self._deploy_exit_server(runner, config_id, plan, server_hops, downstream)
                if hop_status:
                    for hop in sorted(server_hops, key=lambda h: h.index):
                        hop_status.mark_done(hop.id)
            else:
                downstream = self._deploy_relay_server(runner, config_id, plan, server_hops, downstream)
                if hop_status:
                    for hop in sorted(server_hops, key=lambda h: h.index):
                        hop_status.mark_done(hop.id)

        if needs_wg_link(plan):
            self._apply_wg_link_forward(runners, plan)

        if not client_config:
            raise RuntimeError("Не удалось получить клиентский конфиг точки входа")

        return ChainDeployResult(
            client_config=client_config,
            instance_path=entry_path,
            container_name=entry_container,
            meta=_chain_result_meta(plan, client_config, config_id=config_id),
            bypass_meta=bypass_meta or None,
        )

    def _instance(self, runner: RemoteRunner, config_id: str, suffix: str = "") -> str:
        path = _chain_dir(config_id, runner.target.is_local, self.data_dir)
        return f"{path}{suffix}"

    def _gateway_depends_on(self, entry_protocol: str) -> str | None:
        if entry_protocol == "awg":
            return "awg"
        if entry_protocol == "wg":
            return "wg"
        if entry_protocol == "openvpn":
            return "openvpn-entry"
        return None

    def _deploy_exit_server(
        self,
        runner: RemoteRunner,
        config_id: str,
        plan: ChainPlan,
        hops: list[HopSpec],
        _downstream: ExitEndpoint | None,
    ) -> ExitEndpoint:
        exit_hop = hops[-1]
        path = self._instance(runner, config_id, _host_path_suffix(runner, "exit"))
        mkdir_remote(runner, path)

        if exit_hop.protocol == "xray":
            return self._deploy_xray_socks_exit(runner, path, config_id, exit_hop)
        if exit_hop.protocol == "tor":
            return self._deploy_tor_exit(runner, path, config_id, exit_hop)
        if exit_hop.protocol in ("awg", "wg"):
            self._deploy_nat_exit(runner, path, config_id, exit_hop)
            return self._deploy_xray_socks_exit(runner, f"{path}/upstream", config_id, exit_hop)
        if exit_hop.protocol == "openvpn":
            self._deploy_ovpn_exit(runner, path, config_id, exit_hop, plan)
            return self._deploy_xray_socks_exit(runner, f"{path}/upstream", config_id, exit_hop)
        raise ValueError(f"Unsupported exit protocol: {exit_hop.protocol}")

    def _xray_socks_exit_bundle(
        self, path: str, config_id: str, hop: HopSpec, local_host: bool = False
    ) -> tuple[ExitEndpoint, str, list[tuple[str, str, int]]]:
        user = secrets.token_urlsafe(8)
        password = secrets.token_urlsafe(16)
        xray_cfg = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "socks-exit",
                    "port": XRAY_EXIT_PORT,
                    "listen": "0.0.0.0" if not local_host else "127.0.0.1",
                    "protocol": "socks",
                    "settings": {"auth": "password", "accounts": [{"user": user, "pass": password}], "udp": True},
                    "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
                }
            ],
            "outbounds": [{"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}}],
        }
        service = compose_xray_service(
            f"cv-xexit-{_chain_tag(config_id)}",
            path,
            service_name="xray-exit",
            config_file="exit-config.json",
        )
        host = "127.0.0.1" if local_host else hop.server_host
        endpoint = ExitEndpoint(host=host, port=XRAY_EXIT_PORT, auth_user=user, auth_pass=password)
        files = [
            ("exit-config.json", json.dumps(xray_cfg, indent=2), 0o644),
        ]
        return endpoint, service, files

    def _deploy_xray_socks_exit(
        self, runner: RemoteRunner, path: str, config_id: str, hop: HopSpec
    ) -> ExitEndpoint:
        endpoint, service, files = self._xray_socks_exit_bundle(path, config_id, hop)
        for name, content, mode in files:
            runner.upload_text(f"{path}/{name}", content, mode)
        compose = f"services:\n{service}\n"
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        _ensure_compose_images(runner, compose)
        podman_compose_up(runner, path, _compose_project_at(config_id, path))
        return endpoint

    def _needs_wg_link(self, plan: ChainPlan) -> bool:
        return needs_wg_link(plan)

    def _apply_wg_link_forward(self, runners: dict[str, RemoteRunner], plan: ChainPlan) -> None:
        entry_runner = runners[plan.entry.server_id]
        exit_runner = runners[plan.exit.server_id]
        entry_runner.run(
            "iptables -I FORWARD 1 -i tun0 -o wg0 -j ACCEPT 2>/dev/null; "
            "iptables -I FORWARD 2 -i wg0 -o tun0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; true"
        )
        exit_runner.run(
            "iptables -I FORWARD 1 -i wg0 -j ACCEPT 2>/dev/null; "
            f"{wan_wg_forward_up_cmd()}"
        )

    def _deploy_wg_link(
        self,
        entry_runner: RemoteRunner,
        exit_runner: RemoteRunner,
        config_id: str,
        plan: ChainPlan,
    ) -> None:
        exit_path = self._instance(exit_runner, config_id, _host_path_suffix(exit_runner, "link"))
        entry_path = self._instance(entry_runner, config_id, _host_path_suffix(entry_runner, "link"))
        mkdir_remote(entry_runner, entry_path)
        mkdir_remote(exit_runner, exit_path)

        exit_priv, exit_pub = generate_wg_keypair()
        entry_priv, entry_pub = generate_wg_keypair()
        link_port = 51822

        wan_fwd_up = wan_wg_forward_up_cmd()
        wan_fwd_down = wan_wg_forward_down_cmd()
        exit_conf = f"""[Interface]
Address = 10.88.0.1/30
ListenPort = {link_port}
PrivateKey = {exit_priv}
PostUp = {nat_postup("10.88.0.0/30")}; iptables -C FORWARD -i wg0 -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i wg0 -j ACCEPT; {wan_fwd_up}
PostDown = {nat_postdown("10.88.0.0/30")}; iptables -D FORWARD -i wg0 -j ACCEPT 2>/dev/null || true; {wan_fwd_down}
[Peer]
PublicKey = {entry_pub}
AllowedIPs = 10.88.0.2/32, 10.8.0.0/24
"""
        entry_conf = f"""[Interface]
Address = 10.88.0.2/30
PrivateKey = {entry_priv}
Table = off
PostUp = sysctl -w net.ipv4.ip_forward=1 || true; iptables -C FORWARD -i tun0 -o %i -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i tun0 -o %i -j ACCEPT; iptables -C FORWARD -i %i -o tun0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 2 -i %i -o tun0 -m state --state RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -C POSTROUTING -s 10.8.0.0/24 -o %i -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o %i -j MASQUERADE; ip rule del iif tun0 table 100 priority 100 2>/dev/null || true; ip rule del from 10.8.0.0/24 table 100 priority 100 2>/dev/null || true; ip route flush table 100 2>/dev/null || true; ip rule add iif tun0 table 100 priority 100; ip route add 0.0.0.0/1 via 10.88.0.1 dev %i table 100; ip route add 128.0.0.0/1 via 10.88.0.1 dev %i table 100
PostDown = ip rule del iif tun0 table 100 priority 100 2>/dev/null || true; ip route flush table 100 2>/dev/null || true; iptables -D FORWARD -i tun0 -o %i -j ACCEPT 2>/dev/null || true; iptables -D FORWARD -i %i -o tun0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true; iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o %i -j MASQUERADE 2>/dev/null || true
[Peer]
PublicKey = {exit_pub}
Endpoint = {plan.exit.server_host}:{link_port}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
        exit_compose = (
            "services:\n"
            + compose_wg_service(
                f"cv-link-exit-{_chain_tag(config_id)}",
                f"{exit_path}/wg-link.conf",
                service_name="wg-link",
            )
            + "\n"
        )
        entry_compose = (
            "services:\n"
            + compose_wg_service(
                f"cv-link-entry-{_chain_tag(config_id)}",
                f"{entry_path}/wg-link.conf",
                service_name="wg-link",
            )
            + "\n"
        )
        exit_runner.upload_text(f"{exit_path}/wg-link.conf", exit_conf, 0o600)
        exit_runner.upload_text(f"{exit_path}/docker-compose.yml", exit_compose)
        entry_runner.upload_text(f"{entry_path}/wg-link.conf", entry_conf, 0o600)
        entry_runner.upload_text(f"{entry_path}/docker-compose.yml", entry_compose)
        _ensure_compose_images(exit_runner, exit_compose)
        _ensure_compose_images(entry_runner, entry_compose)
        podman_compose_up(exit_runner, exit_path, f"{_compose_project(config_id)}-link-exit")
        podman_compose_up(entry_runner, entry_path, f"{_compose_project(config_id)}-link-entry")

    def _deploy_nat_exit(self, runner: RemoteRunner, path: str, config_id: str, hop: HopSpec) -> ExitEndpoint:
        if hop.protocol == "awg":
            _, _, awg_conf = self._awg_entry_bundle(path, config_id, hop, "10.9.1.0/24")
            conf_path = f"{path}/awg0.conf"
            runner.upload_text(conf_path, awg_conf, 0o600)
            ensure_awg_on_host(runner)
            awg_quick_up(runner, conf_path)
            return ExitEndpoint(host=hop.server_host, port=55424)
        return self._deploy_wg_exit(runner, path, config_id, hop)

    def _deploy_wg_exit(self, runner: RemoteRunner, path: str, config_id: str, hop: HopSpec) -> ExitEndpoint:
        _, pub = generate_wg_keypair()
        server_priv, server_pub = generate_wg_keypair()
        relay_priv, relay_pub = generate_wg_keypair()
        subnet = "10.77.0.0/30"
        wg_conf = f"""[Interface]
Address = 10.77.0.1/30
ListenPort = 51821
PrivateKey = {server_priv}
PostUp = {nat_postup("10.77.0.0/30")}
PostDown = {nat_postdown("10.77.0.0/30")}
[Peer]
PublicKey = {relay_pub}
AllowedIPs = 10.77.0.2/32
"""
        compose = (
            "services:\n"
            + compose_wg_service(
                f"cv-wgexit-{_chain_tag(config_id)}",
                f"{path}/wg.conf",
                service_name="wg-exit",
            )
            + "\n"
        )
        runner.upload_text(f"{path}/wg.conf", wg_conf, 0o600)
        runner.upload_text(f"{path}/relay.key", relay_priv, 0o600)
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        _ensure_compose_images(runner, compose)
        podman_compose_up(runner, path, _compose_project_at(config_id, path))
        return ExitEndpoint(host=hop.server_host, port=51821)

    def _deploy_ovpn_exit(
        self, runner: RemoteRunner, path: str, config_id: str, hop: HopSpec, plan: ChainPlan | None = None
    ) -> ExitEndpoint:
        transport = _ovpn_transport(hop, plan=plan)
        container = f"cv-ovpn-exit-{_chain_tag(config_id)}"
        ensure_openvpn_image(runner)
        mkdir_remote(runner, path)
        runner.upload_text(f"{path}/server.conf", server_conf_content(transport))
        compose = f"services:\n{compose_service(container, path, service_name='openvpn-exit')}\n"
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        ensure_openvpn_port_free(runner, chain_tag=_chain_tag(config_id))
        podman_compose_up(runner, path, _compose_project_at(config_id, path))
        provision_openvpn_instance(runner, path, container, transport)
        return ExitEndpoint(host=hop.server_host, port=1194)

    def _deploy_tor_exit(self, runner: RemoteRunner, path: str, config_id: str, hop: HopSpec) -> ExitEndpoint:
        torrc = """ClientOnly 1
SocksPort 0.0.0.0:9050
TransPort 0.0.0.0:9040
DNSPort 0.0.0.0:9053
VirtualAddrNetworkIPv4 10.192.0.0/10
AutomapHostsOnResolve 1
Log err file /dev/null
"""
        compose = (
            "services:\n"
            + compose_tor_service(
                f"cv-tor-exit-{_chain_tag(config_id)}",
                path,
                service_name="tor-exit",
                tor_data_volume=True,
                privileged=True,
            )
            + "\nvolumes:\n  tor-data:\n"
        )
        runner.upload_text(f"{path}/torrc", torrc)
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        _ensure_compose_images(runner, compose)
        podman_compose_up(runner, path, _compose_project_at(config_id, path))
        return ExitEndpoint(
            host=hop.server_host,
            port=TOR_SOCKS_PORT,
            tor_dynamic_exit=True,
        )

    def _deploy_relay_server(
        self,
        runner: RemoteRunner,
        config_id: str,
        plan: ChainPlan,
        hops: list[HopSpec],
        downstream: ExitEndpoint | None,
    ) -> ExitEndpoint:
        if not downstream:
            raise RuntimeError("Relay server requires downstream endpoint")
        path = self._instance(runner, config_id, _host_path_suffix(runner, "relay"))
        mkdir_remote(runner, path)
        if not hops:
            raise ValueError("Relay server has no hops")
        for hop in hops:
            if hop.protocol == "tor":
                downstream = self._deploy_tor_gateway(
                    runner, path, config_id, downstream, entry_subnet=None, hop=hop
                )
            elif hop.protocol == "xray":
                downstream = self._deploy_xray_relay(runner, path, config_id, hop, downstream)
            else:
                                                                              
                downstream = self._deploy_xray_relay(runner, path, config_id, hop, downstream)
        return downstream

    def _deploy_entry_awg_only(
        self,
        runner: RemoteRunner,
        config_id: str,
        plan: ChainPlan,
    ) -> tuple[str, str, dict[str, Any]]:
        path = self._instance(runner, config_id, _host_path_suffix(runner, "entry"))
        mkdir_remote(runner, path)
        subnet = "10.9.1.0/24"
        client_conf, _, awg_conf = self._awg_entry_bundle(
            path, config_id, plan.entry, subnet, gateway_mode=True
        )
        conf_path = f"{path}/awg0.conf"
        runner.upload_text(conf_path, awg_conf, 0o600)
        ensure_awg_on_host(runner)
        awg_quick_up(runner, conf_path)
        return client_conf, path, {}

    def _deploy_entry_compose(
        self,
        runner: RemoteRunner,
        config_id: str,
        plan: ChainPlan,
        hops: list[HopSpec],
        downstream: ExitEndpoint | None,
        *,
        entry_path: str,
        client_conf: str = "",
    ) -> tuple[str, str, dict[str, Any]]:
        path = entry_path
        bypass_meta: dict[str, Any] = {}
        local_tor = any(h.protocol == "tor" for h in hops)
        entry_if = "awg0"
        subnet = "10.9.1.0/24"
        services: list[str] = []
        use_gateway = entry_needs_gateway(plan, downstream is not None, local_tor)

        if local_tor:
            tor_svc, tor_files = self._tor_sidecar_bundle(path, config_id)
            for name, content, mode in tor_files:
                runner.upload_text(f"{path}/{name}", content, mode)
            services.append(tor_svc)

        if use_gateway:
            gw_svc = self._gateway_files(
                path,
                config_id,
                downstream or ExitEndpoint(host="127.0.0.1", port=XRAY_EXIT_PORT),
                entry_if=entry_if,
                entry_subnet=subnet,
                local_tor=local_tor or bool(downstream and downstream.direct),
                depends_on=None,
            )
            self._upload_gateway_files(runner, path)
            services.append(gw_svc)

        if services:
            compose = "services:\n" + "\n".join(services)
            if any("tor-data" in s for s in services):
                compose += "\nvolumes:\n  tor-data:\n"
            _ensure_compose_images(runner, compose)
            runner.upload_text(f"{path}/docker-compose.yml", compose)
            podman_compose_up(runner, path, _compose_project_at(config_id, path))

        return client_conf, path, bypass_meta

    def _deploy_entry_server(
        self,
        runner: RemoteRunner,
        config_id: str,
        plan: ChainPlan,
        hops: list[HopSpec],
        downstream: ExitEndpoint | None,
        base_path: str | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        path = base_path or self._instance(runner, config_id, _host_path_suffix(runner, "entry"))
        mkdir_remote(runner, path)
        entry = hops[0]
        bypass_meta: dict[str, Any] = {}
        local_tor = any(h.protocol == "tor" for h in hops)
        entry_if = (
            "awg0"
            if entry.protocol == "awg"
            else "wg0"
            if entry.protocol == "wg"
            else "tun0"
            if entry.protocol == "openvpn"
            else None
        )
        subnet = (
            "10.9.1.0/24"
            if entry.protocol == "awg"
            else "10.66.66.0/24"
            if entry.protocol == "wg"
            else "10.8.0.0/24"
            if entry.protocol == "openvpn"
            else None
        )

        client_conf = ""
        services: list[str] = []
        use_gateway = entry_needs_gateway(plan, downstream is not None, local_tor)

        if entry.protocol == "awg":
            client_conf, _, awg_conf = self._awg_entry_bundle(
                path, config_id, plan.entry, subnet or "10.9.1.0/24", gateway_mode=use_gateway
            )
            conf_path = f"{path}/awg0.conf"
            runner.upload_text(conf_path, awg_conf, 0o600)
            ensure_awg_on_host(runner)
            awg_quick_up(runner, conf_path)
        elif entry.protocol == "wg":
            client_conf, wg_svc, wg_conf = self._wg_entry_bundle(
                path, config_id, plan.entry, gateway_mode=use_gateway
            )
            runner.upload_text(f"{path}/wg0.conf", wg_conf, 0o600)
            services.append(wg_svc)
        elif entry.protocol == "xray":
            client_conf, xray_svc, xray_files, hop_bypass_meta = self._xray_entry_bundle(
                path,
                config_id,
                plan.entry,
                downstream,
                local_tor,
            )
            for name, content, mode in xray_files:
                runner.upload_text(f"{path}/{name}", content, mode)
            if local_tor:
                tor_svc, tor_files = self._tor_sidecar_bundle(path, config_id)
                for name, content, mode in tor_files:
                    runner.upload_text(f"{path}/{name}", content, mode)
                services.append(tor_svc)
            services.append(xray_svc)
            bypass_meta = hop_bypass_meta
        elif entry.protocol == "openvpn":
            client_conf, ovpn_svc, ovpn_files = self._ovpn_entry_bundle(
                path,
                config_id,
                plan.entry,
                skip_wan_nat=needs_wg_link(plan) or use_gateway,
            )
            for name, content, mode in ovpn_files:
                runner.upload_text(f"{path}/{name}", content, mode)
            services.append(ovpn_svc)
        else:
            raise ValueError(f"Unsupported entry: {entry.protocol}")

        if use_gateway:
            gw_svc = self._gateway_files(
                path,
                config_id,
                downstream or ExitEndpoint(host="127.0.0.1", port=XRAY_EXIT_PORT),
                entry_if=entry_if,
                entry_subnet=subnet,
                local_tor=local_tor or bool(downstream and downstream.direct),
                depends_on=None if entry.protocol == "awg" else self._gateway_depends_on(entry.protocol),
            )
            self._upload_gateway_files(runner, path)
            services.append(gw_svc)

        if services:
            compose = "services:\n" + "\n".join(services)
            if any("tor-data" in s for s in services):
                compose += "\nvolumes:\n  tor-data:\n"
            _ensure_compose_images(runner, compose)
            runner.upload_text(f"{path}/docker-compose.yml", compose)
            if entry.protocol == "openvpn":
                ensure_openvpn_port_free(runner, chain_tag=_chain_tag(config_id))
            podman_compose_up(runner, path, _compose_project_at(config_id, path))
            if entry.protocol == "openvpn":
                ensure_openvpn_image(runner)
                provision_openvpn_instance(
                    runner,
                    path,
                    f"cv-ovpn-entry-{_chain_tag(config_id)}",
                    _ovpn_transport(plan.entry),
                )
                client_conf = build_client_ovpn(
                    runner,
                    path,
                    plan.entry.server_host,
                    _ovpn_transport(plan.entry),
                )
                if needs_wg_link(plan):
                    runner.run(
                        "WAN=$(ip -4 route show default | awk '/default/{print $5;exit}'); "
                        "iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o \"$WAN\" -j MASQUERADE 2>/dev/null || true"
                    )
                    runner.run(f"bash -s << 'EOF'\n{HOST_GATEWAY_IPTABLES_CLEANUP}\nEOF")

        return client_conf, path, bypass_meta

    def _awg_entry_bundle(
        self,
        path: str,
        config_id: str,
        hop: HopSpec,
        subnet: str,
        *,
        gateway_mode: bool = False,
    ) -> tuple[str, str, str]:
        server_priv, server_pub = generate_wg_keypair()
        client_priv, client_pub = generate_wg_keypair()
        awg_ver = getattr(hop, "awg_version", None) or (hop.get("awgVersion") if isinstance(hop, dict) else "awg2.0")
        awg = generate_awg_params(version=awg_ver)
        psk = generate_wg_psk()
        if gateway_mode:
            post_up = "sysctl -w net.ipv4.ip_forward=1 || true"
            post_down = "true"
        else:
            post_up = nat_postup(subnet)
            post_down = nat_postdown(subnet)
        awg_conf = build_awg_server_conf(
            server_priv=server_priv,
            client_pub=client_pub,
            client_ip="10.9.1.2",
            listen_port=55424,
            post_up=post_up,
            post_down=post_down,
            params=awg,
            preshared_key=psk,
        )
        client_conf = build_awg_client_conf(
            client_priv=client_priv,
            server_pub=server_pub,
            server_host=hop.server_host,
            listen_port=55424,
            dns="10.9.1.1",
            params=awg,
            preshared_key=psk,
        )
        service = ""
        return client_conf, service, awg_conf

    def _wg_entry_bundle(
        self, path: str, config_id: str, hop: HopSpec, *, gateway_mode: bool = False
    ) -> tuple[str, str, str]:
        server_priv, server_pub = generate_wg_keypair()
        client_priv, client_pub = generate_wg_keypair()
        if gateway_mode:
            post_up = "sysctl -w net.ipv4.ip_forward=1 || true"
            post_down = "true"
        else:
            post_up = nat_postup("10.66.66.0/24")
            post_down = nat_postdown("10.66.66.0/24")
        wg_conf = f"""[Interface]
Address = 10.66.66.1/24
ListenPort = 51820
PrivateKey = {server_priv}
PostUp = {post_up}
PostDown = {post_down}
[Peer]
PublicKey = {client_pub}
AllowedIPs = 10.66.66.2/32
"""
        client_conf = f"""[Interface]
PrivateKey = {client_priv}
Address = 10.66.66.2/32
DNS = 1.1.1.1
[Peer]
PublicKey = {server_pub}
Endpoint = {hop.server_host}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
        service = compose_wg_service(
            f"cv-chain-wg-{_chain_tag(config_id)}",
            f"{path}/wg0.conf",
        )
        return client_conf, service, wg_conf

    def _ovpn_entry_bundle(
        self, path: str, config_id: str, hop: HopSpec, *, skip_wan_nat: bool = False
    ) -> tuple[str, str, list[tuple[str, str, int]]]:
        transport = _ovpn_transport(hop)
        container = f"cv-ovpn-entry-{_chain_tag(config_id)}"
        client_stub = (
            f"client\ndev tun\nproto {transport}\nremote {hop.server_host} 1194\n"
            f"resolv-retry infinite\nnobind\npersist-key\npersist-tun\n"
            f"remote-cert-tls server\ncipher AES-256-GCM\nverb 3\n"
        )
        service = compose_service(
            container, path, service_name="openvpn-entry", skip_wan_nat=skip_wan_nat
        )
        files = [("server.conf", server_conf_content(transport), 0o644)]
        return client_stub, service, files

    def _gateway_files(
        self,
        path: str,
        config_id: str,
        downstream: ExitEndpoint,
        entry_if: str | None,
        entry_subnet: str | None,
        local_tor: bool,
        depends_on: str | None,
        *,
        relay_inbound: bool = False,
    ) -> str:
        via_tor = local_tor or downstream.direct
        listen_ip = (
            "10.9.1.1"
            if entry_if == "awg0"
            else "10.66.66.1"
            if entry_if == "wg0"
            else "10.8.0.1"
            if entry_if == "tun0"
            else "127.0.0.1"
        )
        xray_cfg = self._xray_chain_config(
            downstream,
            via_tor=via_tor,
            entry_redirect=entry_subnet is not None,
            awg_ip=listen_ip,
            relay_inbound=relay_inbound,
        )
        if entry_if == "awg0":
            torrc = f"""DataDirectory /var/lib/tor
TransPort {listen_ip}:9040
SocksPort 127.0.0.1:9050
DNSPort {listen_ip}:9053
VirtualAddrNetworkIPv4 10.192.0.0/10
AutomapHostsOnResolve 1
Log err file /dev/null
"""
        else:
            torrc = """DataDirectory /var/lib/tor
SocksPort 127.0.0.1:9050
DNSPort 127.0.0.1:9053
VirtualAddrNetworkIPv4 10.192.0.0/10
AutomapHostsOnResolve 1
Log err file /dev/null
"""
        redirect_block = ""
        if entry_if and entry_subnet:
            redirect_block = f"""for i in $(seq 1 30); do ip link show {entry_if} >/dev/null 2>&1 && break; sleep 2; done
iptables -t nat -C PREROUTING -i {entry_if} -p tcp -s {entry_subnet} -j REDIRECT --to-ports {XRAY_ENTRY_PORT} 2>/dev/null ||
iptables -t nat -A PREROUTING -i {entry_if} -p tcp -s {entry_subnet} -j REDIRECT --to-ports {XRAY_ENTRY_PORT}
"""
            if local_tor:
                redirect_block += f"""iptables -t nat -C PREROUTING -i {entry_if} -p udp -s {entry_subnet} --dport 53 -j REDIRECT --to-ports 9053 2>/dev/null ||
iptables -t nat -A PREROUTING -i {entry_if} -p udp -s {entry_subnet} --dport 53 -j REDIRECT --to-ports 9053
"""
        elif entry_if == "tun0" and entry_subnet:
            redirect_block = f"""for i in $(seq 1 30); do ip link show tun0 >/dev/null 2>&1 && break; sleep 2; done
iptables -t nat -C PREROUTING -i tun0 -p tcp -s {entry_subnet} -j REDIRECT --to-ports {XRAY_ENTRY_PORT} 2>/dev/null ||
iptables -t nat -A PREROUTING -i tun0 -p tcp -s {entry_subnet} -j REDIRECT --to-ports {XRAY_ENTRY_PORT}
"""
        tor_start = ""
        if local_tor:
            if depends_on:
                tor_start = """for _ in $(seq 1 60); do
  nc -z 127.0.0.1 9050 2>/dev/null && break
  sleep 2
done
"""
            else:
                tor_start = """su-exec tor tor -f /etc/tor/torrc &
for _ in $(seq 1 90); do
  nc -z 127.0.0.1 9050 2>/dev/null && break
  sleep 2
done
"""
        ovpn_forward = ""
        if entry_if == "tun0" and entry_subnet:
            ovpn_forward = f"""WAN=$(ip -4 route show default | awk '/default/ {{print $5; exit}}')
if [ -n "$WAN" ]; then
  iptables -C FORWARD -i tun0 -o "$WAN" -p udp --dport 53 -j ACCEPT 2>/dev/null ||
    iptables -A FORWARD -i tun0 -o "$WAN" -p udp --dport 53 -j ACCEPT
  iptables -C FORWARD -i "$WAN" -o tun0 -p udp --sport 53 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null ||
    iptables -A FORWARD -i "$WAN" -o tun0 -p udp --sport 53 -m state --state RELATED,ESTABLISHED -j ACCEPT
  iptables -t nat -C POSTROUTING -s {entry_subnet} -o "$WAN" -p udp --dport 53 -j MASQUERADE 2>/dev/null ||
    iptables -t nat -A POSTROUTING -s {entry_subnet} -o "$WAN" -p udp --dport 53 -j MASQUERADE
fi
"""
        stale_xray_kill = """for _port in 12345 1080 10808; do
  for pid in $(ss -tlnp 2>/dev/null | grep ":${_port} " | sed -n 's/.*pid=\\([0-9]*\\).*/\\1/p' | sort -u); do
    kill "$pid" 2>/dev/null || true
  done
done
sleep 1
"""
        start_sh = f"""#!/bin/bash
set -uo pipefail
sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true
sysctl -w net.ipv4.conf.all.route_localnet=1 2>/dev/null || true
{stale_xray_kill}{redirect_block}{ovpn_forward}{tor_start}xray run -c {GATEWAY_MOUNT}/config.json &
tail -f /dev/null
"""
        self._pending_gateway_files = (xray_cfg, torrc if local_tor else None, start_sh)
        return compose_gateway_service(
            f"cv-gateway-{_chain_tag(config_id)}",
            path,
            depends_on=depends_on,
            tor_data_volume=local_tor,
        )

    def _upload_gateway_files(self, runner: RemoteRunner, path: str) -> None:
        if not hasattr(self, "_pending_gateway_files"):
            return
        xray_cfg, torrc, start_sh = self._pending_gateway_files
        runner.upload_text(f"{path}/config.json", json.dumps(xray_cfg, indent=2))
        if torrc:
            runner.upload_text(f"{path}/torrc", torrc)
        runner.upload_text(f"{path}/start.sh", start_sh, 0o755)
        del self._pending_gateway_files

    def _tor_sidecar_bundle(
        self, path: str, config_id: str
    ) -> tuple[str, list[tuple[str, str, int]]]:
        torrc = """DataDirectory /var/lib/tor
SocksPort 127.0.0.1:9050
DNSPort 127.0.0.1:9053
VirtualAddrNetworkIPv4 10.192.0.0/10
AutomapHostsOnResolve 1
Log err file /dev/null
"""
        service = compose_tor_service(
            f"cv-tor-sidecar-{_chain_tag(config_id)}",
            f"{path}/tor-sidecar",
            service_name="tor-sidecar",
            tor_data_volume=True,
            privileged=True,
        )
        files = [
            ("tor-sidecar/torrc", torrc, 0o644),
        ]
        return service, files

    def _deploy_single_server_chain(
        self,
        runner: RemoteRunner,
        config_id: str,
        plan: ChainPlan,
        hops: list[HopSpec],
    ) -> tuple[str, str, dict[str, Any]]:
        path = self._instance(runner, config_id)
        mkdir_remote(runner, path)
        services: list[str] = []
        downstream: ExitEndpoint | None = None
        bypass_meta: dict[str, Any] = {}
        exit_hop = hops[-1]

        if exit_hop.protocol == "xray":
            downstream, exit_svc, exit_files = self._xray_socks_exit_bundle(path, config_id, exit_hop, local_host=True)
            for name, content, mode in exit_files:
                runner.upload_text(f"{path}/{name}", content, mode)
            services.append(exit_svc)
        elif exit_hop.protocol == "tor":
            downstream = ExitEndpoint(host="127.0.0.1", port=TOR_SOCKS_PORT, direct=True)
        elif exit_hop.protocol in ("awg", "wg"):
            if exit_hop.protocol == "awg":
                _, _, exit_conf = self._awg_entry_bundle(path, config_id, exit_hop, "10.9.1.0/24")
                conf_path = f"{path}/awg0.conf"
                runner.upload_text(conf_path, exit_conf, 0o600)
                ensure_awg_on_host(runner)
                awg_quick_up(runner, conf_path)
            else:
                _, exit_svc, exit_conf = self._wg_entry_bundle(path, config_id, exit_hop)
                runner.upload_text(f"{path}/wg0.conf", exit_conf, 0o600)
                services.append(exit_svc.replace("cv-chain-", "cv-exit-"))
            downstream, bridge_svc, bridge_files = self._xray_socks_exit_bundle(
                path, config_id, exit_hop, local_host=True
            )
            for name, content, mode in bridge_files:
                runner.upload_text(f"{path}/{name}", content, mode)
            services.append(bridge_svc)
        elif exit_hop.protocol == "openvpn":
            _, ovpn_svc, ovpn_files = self._ovpn_entry_bundle(path, config_id, exit_hop)
            for name, content, mode in ovpn_files:
                runner.upload_text(f"{path}/{name}", content, mode)
            services.append(
                ovpn_svc.replace("openvpn-entry", "openvpn-exit").replace("cv-ovpn-entry", "cv-ovpn-exit")
            )
            downstream, bridge_svc, bridge_files = self._xray_socks_exit_bundle(
                path, config_id, exit_hop, local_host=True
            )
            for name, content, mode in bridge_files:
                runner.upload_text(f"{path}/{name}", content, mode)
            services.append(bridge_svc)
        else:
            raise ValueError(f"Unsupported single-server exit: {exit_hop.protocol}")

        entry = hops[0]
        entry_if = (
            "awg0"
            if entry.protocol == "awg"
            else "wg0"
            if entry.protocol == "wg"
            else "tun0"
            if entry.protocol == "openvpn"
            else None
        )
        subnet = (
            "10.9.1.0/24"
            if entry.protocol == "awg"
            else "10.66.66.0/24"
            if entry.protocol == "wg"
            else "10.8.0.0/24"
            if entry.protocol == "openvpn"
            else None
        )
        local_tor = any(h.protocol == "tor" for h in hops)

        if entry_needs_gateway(plan, downstream is not None, local_tor) and entry.protocol != "xray":
            gw_svc = self._gateway_files(
                path,
                config_id,
                downstream or ExitEndpoint(host="127.0.0.1", port=XRAY_EXIT_PORT),
                entry_if=entry_if,
                entry_subnet=subnet,
                local_tor=local_tor or bool(downstream and downstream.direct),
                depends_on=None if entry.protocol == "awg" else self._gateway_depends_on(entry.protocol),
            )
            self._upload_gateway_files(runner, path)
            services.append(gw_svc)

        if entry.protocol == "awg":
            client_conf, _, awg_conf = self._awg_entry_bundle(path, config_id, plan.entry, subnet or "10.9.1.0/24")
            conf_path = f"{path}/awg0.conf"
            runner.upload_text(conf_path, awg_conf, 0o600)
            ensure_awg_on_host(runner)
            awg_quick_up(runner, conf_path)
        elif entry.protocol == "wg":
            client_conf, entry_svc, wg_conf = self._wg_entry_bundle(path, config_id, plan.entry)
            runner.upload_text(f"{path}/wg0.conf", wg_conf, 0o600)
            services.insert(0, entry_svc)
        elif entry.protocol == "openvpn":
            client_conf, entry_svc, entry_files = self._ovpn_entry_bundle(path, config_id, plan.entry)
            for name, content, mode in entry_files:
                runner.upload_text(f"{path}/{name}", content, mode)
            services.insert(0, entry_svc)
        elif entry.protocol == "xray":
            client_conf, xray_svc, xray_files, hop_bypass_meta = self._xray_entry_bundle(
                path, config_id, plan.entry, downstream, local_tor
            )
            for name, content, mode in xray_files:
                runner.upload_text(f"{path}/{name}", content, mode)
            if local_tor:
                tor_svc, tor_files = self._tor_sidecar_bundle(path, config_id)
                for name, content, mode in tor_files:
                    runner.upload_text(f"{path}/{name}", content, mode)
                services.append(tor_svc)
            services.insert(0, xray_svc)
            bypass_meta = hop_bypass_meta
        else:
            raise ValueError(f"Unsupported single-server entry: {entry.protocol}")

        compose = "services:\n" + "\n".join(services)
        if local_tor or "tor-data" in compose:
            compose += "\nvolumes:\n  tor-data:\n"
        _ensure_compose_images(runner, compose)
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        if entry.protocol == "openvpn" or exit_hop.protocol == "openvpn":
            ensure_openvpn_port_free(runner, chain_tag=_chain_tag(config_id))
        podman_compose_up(runner, path, _compose_project_at(config_id, path))
        ensure_openvpn_image(runner)
        if entry.protocol == "openvpn":
            provision_openvpn_instance(
                runner,
                path,
                f"cv-ovpn-entry-{_chain_tag(config_id)}",
                _ovpn_transport(entry),
            )
            client_conf = build_client_ovpn(
                runner,
                path,
                plan.entry.server_host,
                _ovpn_transport(entry),
            )
        if exit_hop.protocol == "openvpn":
            provision_openvpn_instance(
                runner,
                path,
                f"cv-ovpn-exit-{_chain_tag(config_id)}",
                _ovpn_transport(exit_hop),
            )
        return client_conf, path, bypass_meta

    def _entry_bundle_for(
        self,
        entry: HopSpec,
        path: str,
        config_id: str,
        hop: HopSpec,
        subnet: str | None,
    ) -> tuple[str, str, Any]:
        if entry.protocol == "awg":
            return self._awg_entry_bundle(path, config_id, hop, subnet or "10.9.1.0/24")
        if entry.protocol == "wg":
            return self._wg_entry_bundle(path, config_id, hop)
        if entry.protocol == "openvpn":
            return self._ovpn_entry_bundle(path, config_id, hop)
        raise ValueError(f"Unknown entry: {entry.protocol}")

    def _xray_chain_config(
        self,
        downstream: ExitEndpoint,
        via_tor: bool,
        entry_redirect: bool,
        awg_ip: str = "10.9.1.1",
        relay_inbound: bool = False,
    ) -> dict:
        exit_servers: list[dict] = [{"address": downstream.host, "port": downstream.port}]
        if downstream.auth_user:
            exit_servers[0]["users"] = [{"user": downstream.auth_user, "pass": downstream.auth_pass}]

        outbounds: list[dict] = []
        if downstream.direct:
            if via_tor:
                exit_out: dict = {
                    "tag": "exit",
                    "protocol": "socks",
                    "settings": {"servers": [{"address": "127.0.0.1", "port": TOR_SOCKS_PORT}]},
                }
            else:
                exit_out = {
                    "tag": "exit",
                    "protocol": "freedom",
                    "settings": {"domainStrategy": "UseIPv4"},
                }
        elif getattr(downstream, "tor_dynamic_exit", False):
            exit_out = {
                "tag": "exit",
                "protocol": "socks",
                "settings": {"servers": exit_servers},
            }
        else:
            if via_tor:
                outbounds.append(
                    {
                        "tag": "tor",
                        "protocol": "socks",
                        "settings": {"servers": [{"address": "127.0.0.1", "port": TOR_SOCKS_PORT}]},
                    }
                )
            exit_out = {
                "tag": "exit",
                "protocol": "socks",
                "settings": {"servers": exit_servers},
            }
            if via_tor:
                exit_out["proxySettings"] = {"tag": "tor"}
        outbounds.extend(
            [
                exit_out,
                {"tag": "direct", "protocol": "freedom"},
            ]
        )

        inbounds: list[dict] = []
        if relay_inbound:
            inbounds.append(
                {
                    "tag": "relay-socks",
                    "port": XRAY_SOCKS_PORT,
                    "listen": "0.0.0.0",
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": False},
                    "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
                }
            )
        if entry_redirect:
            inbounds.append(
                {
                    "tag": "entry-tcp",
                    "port": XRAY_ENTRY_PORT,
                    "listen": "0.0.0.0",
                    "protocol": "dokodemo-door",
                    "settings": {"network": "tcp", "followRedirect": True},
                    "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
                }
            )
            inbounds.append(
                {
                    "tag": "socks-in",
                    "port": XRAY_SOCKS_PORT,
                    "listen": awg_ip,
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": False},
                    "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
                }
            )

        rules = []
        if relay_inbound:
            rules.append(
                {"type": "field", "inboundTag": ["relay-socks"], "outboundTag": "exit"}
            )
        if entry_redirect:
            rules.append(
                {
                    "type": "field",
                    "network": "tcp",
                    "inboundTag": ["entry-tcp", "socks-in"],
                    "outboundTag": "exit",
                }
            )

        return {"log": {"loglevel": "warning"}, "inbounds": inbounds, "outbounds": outbounds, "routing": {"domainStrategy": "AsIs", "rules": rules}}

    def _finalize_entry_chain_routing(
        self,
        xray_cfg: dict,
        downstream: ExitEndpoint | None,
        *,
        inbound_tag: str = "vless-in",
        via_tor: bool,
    ) -> None:
        if via_tor and downstream and not downstream.direct:
            exit_servers: list[dict] = [{"address": downstream.host, "port": downstream.port}]
            if downstream.auth_user:
                exit_servers[0]["users"] = [
                    {"user": downstream.auth_user, "pass": downstream.auth_pass}
                ]
            xray_cfg["outbounds"].append(
                {
                    "tag": "exit-direct",
                    "protocol": "socks",
                    "settings": {"servers": exit_servers},
                }
            )
            xray_cfg["routing"] = {
                "domainStrategy": "AsIs",
                "rules": [
                    {
                        "type": "field",
                        "network": "tcp",
                        "inboundTag": [inbound_tag],
                        "outboundTag": "exit",
                    },
                    {
                        "type": "field",
                        "network": "udp",
                        "inboundTag": [inbound_tag],
                        "outboundTag": "exit-direct",
                    },
                ],
            }
            return
        xray_cfg.setdefault("routing", {"domainStrategy": "AsIs", "rules": []})
        xray_cfg["routing"]["rules"].insert(
            0,
            {"type": "field", "inboundTag": [inbound_tag], "outboundTag": "exit"},
        )

    def _deploy_tor_gateway(
        self,
        runner: RemoteRunner,
        path: str,
        config_id: str,
        downstream: ExitEndpoint,
        entry_subnet: str | None,
        hop: HopSpec | None = None,
    ) -> ExitEndpoint:
        local_tor = True
        gw_svc = self._gateway_files(
            path,
            config_id,
            downstream,
            entry_if="awg0" if entry_subnet else None,
            entry_subnet=entry_subnet,
            local_tor=True,
            depends_on=None if entry_subnet else None,
            relay_inbound=entry_subnet is None,
        )
        self._upload_gateway_files(runner, path)
        compose = f"services:\n{gw_svc}\nvolumes:\n  tor-data:\n"
        _ensure_compose_images(runner, compose)
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        podman_compose_up(runner, path, _compose_project_at(config_id, path))
        host = runner.target.host if hop else "127.0.0.1"
        if entry_subnet:
            return ExitEndpoint(host=host, port=XRAY_SOCKS_PORT, via_tor=True)
        return ExitEndpoint(host=host, port=XRAY_SOCKS_PORT)

    def _deploy_xray_relay(
        self, runner: RemoteRunner, path: str, config_id: str, hop: HopSpec, downstream: ExitEndpoint
    ) -> ExitEndpoint:
        via_tor = downstream.via_tor or downstream.direct
        xray_cfg = self._xray_chain_config(
            downstream, via_tor=via_tor, entry_redirect=False, relay_inbound=True
        )
        compose = (
            "services:\n"
            + compose_xray_service(
                f"cv-xrelay-{_chain_tag(config_id)}",
                path,
                service_name="xray-relay",
                config_file="config.json",
            )
            + "\n"
        )
        runner.upload_text(f"{path}/config.json", json.dumps(xray_cfg, indent=2))
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        _ensure_compose_images(runner, compose)
        podman_compose_up(runner, path, _compose_project_at(config_id, path))
        return ExitEndpoint(host=hop.server_host, port=XRAY_SOCKS_PORT)

    def _deploy_awg_entry(
        self,
        runner: RemoteRunner,
        path: str,
        config_id: str,
        hop: HopSpec,
        downstream: ExitEndpoint | None,
        has_tor: bool,
        subnet: str,
    ) -> tuple[str, str]:
        server_priv, server_pub = generate_wg_keypair()
        client_priv, client_pub = generate_wg_keypair()
        awg_ver = getattr(hop, "awg_version", None) or (hop.get("awgVersion") if isinstance(hop, dict) else "awg2.0")
        awg = generate_awg_params(version=awg_ver)
        psk = generate_wg_psk()
        awg_conf = build_awg_server_conf(
            server_priv=server_priv,
            client_pub=client_pub,
            client_ip="10.9.1.2",
            listen_port=55424,
            post_up=nat_postup(subnet),
            post_down=nat_postdown(subnet),
            params=awg,
            preshared_key=psk,
        )
        client_conf = build_awg_client_conf(
            client_priv=client_priv,
            server_pub=server_pub,
            server_host=hop.server_host,
            listen_port=55424,
            dns="10.9.1.1",
            params=awg,
            preshared_key=psk,
        )
        conf_path = f"{path}/awg0.conf"
        runner.upload_text(conf_path, awg_conf, 0o600)
        ensure_awg_on_host(runner)
        awg_quick_up(runner, conf_path)
        return client_conf, path

    def _deploy_wg_entry(
        self,
        runner: RemoteRunner,
        path: str,
        config_id: str,
        hop: HopSpec,
        downstream: ExitEndpoint | None,
        has_tor: bool,
    ) -> tuple[str, str]:
        server_priv, server_pub = generate_wg_keypair()
        client_priv, client_pub = generate_wg_keypair()
        wg_conf = f"""[Interface]
Address = 10.66.66.1/24
ListenPort = 51820
PrivateKey = {server_priv}
PostUp = {nat_postup("10.66.66.0/24")}
PostDown = {nat_postdown("10.66.66.0/24")}
[Peer]
PublicKey = {client_pub}
AllowedIPs = 10.66.66.2/32
"""
        client_conf = f"""[Interface]
PrivateKey = {client_priv}
Address = 10.66.66.2/32
DNS = 1.1.1.1
[Peer]
PublicKey = {server_pub}
Endpoint = {hop.server_host}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
        compose = (
            "services:\n"
            + compose_wg_service(
                f"cv-chain-wg-{_chain_tag(config_id)}",
                f"{path}/wg0.conf",
            )
            + "\n"
        )
        runner.upload_text(f"{path}/wg0.conf", wg_conf, 0o600)
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        _ensure_compose_images(runner, compose)
        podman_compose_up(runner, path, _compose_project_at(config_id, path))
        return client_conf, path

    def _xray_entry_bundle(
        self,
        path: str,
        config_id: str,
        hop: HopSpec,
        downstream: ExitEndpoint | None,
        local_tor: bool,
    ) -> tuple[str, str, list[tuple[str, str, int]], dict[str, Any]]:
        via_tor = bool(
            downstream
            and ((local_tor and not downstream.direct) or downstream.direct)
        )
        chain_remark = f"Chain-{_chain_tag(config_id)}"
        bypass_meta: dict[str, Any] = {}

        if hop.xray_bypass:
            vless_uuid = new_vless_uuid()
            private_key, public_key = generate_reality_keypair()
            short_id = generate_short_id()
            inbound = build_reality_vless_inbound(vless_uuid, private_key, short_id, port=443)
            if downstream:
                xray_cfg = self._xray_chain_config(downstream, via_tor=via_tor, entry_redirect=False)
                xray_cfg["inbounds"] = [inbound]
                self._finalize_entry_chain_routing(
                    xray_cfg, downstream, inbound_tag="vless-in", via_tor=via_tor
                )
            else:
                xray_cfg = {
                    "log": {"loglevel": "warning"},
                    "inbounds": [inbound],
                    "outbounds": [{"protocol": "freedom"}],
                }
            client_cfg = build_bypass_client_config(
                hop.server_host,
                vless_uuid,
                public_key,
                short_id,
                remark=chain_remark,
            )
            vless_uri = build_vless_reality_uri(
                vless_uuid,
                hop.server_host,
                public_key,
                short_id,
                remark=chain_remark,
            )
            client_conf = f"{json.dumps(client_cfg, indent=2, ensure_ascii=False)}\n\n{vless_uri}\n"
            bypass_meta = bypass_meta_from_keys(
                vless_uuid=vless_uuid,
                public_key=public_key,
                short_id=short_id,
                port=443,
                vless_uri=vless_uri,
            )
            service = compose_xray_service(
                f"cv-xentry-{_chain_tag(config_id)}",
                path,
                service_name="xray-entry",
                config_file="entry-config.json",
                depends_on="tor-sidecar" if local_tor else None,
            )
            files = [("entry-config.json", json.dumps(xray_cfg, indent=2), 0o644)]
            return client_conf, service, files, bypass_meta

        vless_uuid = str(uuid.uuid4())
        cert_pem, key_pem = tls_cert_paths()
        if downstream:
            xray_cfg = self._xray_chain_config(downstream, via_tor=via_tor, entry_redirect=False)
            xray_cfg["inbounds"] = [
                {
                    "tag": "vless-in",
                    "port": 443,
                    "listen": "0.0.0.0",
                    "protocol": "vless",
                    "settings": {"clients": [{"id": vless_uuid}], "decryption": "none"},
                    "streamSettings": {
                        "network": "tcp",
                        "security": "tls",
                        "tlsSettings": {
                            "certificates": [
                                {"certificateFile": cert_pem, "keyFile": key_pem}
                            ]
                        },
                    },
                }
            ]
            self._finalize_entry_chain_routing(
                xray_cfg, downstream, inbound_tag="vless-in", via_tor=via_tor
            )
        else:
            xray_cfg = {
                "log": {"loglevel": "warning"},
                "inbounds": [
                    {
                        "tag": "vless-in",
                        "port": 443,
                        "listen": "0.0.0.0",
                        "protocol": "vless",
                        "settings": {"clients": [{"id": vless_uuid}], "decryption": "none"},
                        "streamSettings": {
                            "network": "tcp",
                            "security": "tls",
                            "tlsSettings": {
                                "certificates": [
                                    {"certificateFile": cert_pem, "keyFile": key_pem}
                                ]
                            },
                        },
                    }
                ],
                "outbounds": [{"protocol": "freedom"}],
            }
        service = compose_xray_service(
            f"cv-xentry-{_chain_tag(config_id)}",
            path,
            service_name="xray-entry",
            config_file="entry-config.json",
            tls_cn=hop.server_host,
            depends_on="tor-sidecar" if local_tor else None,
        )
        vless_uri = (
            f"vless://{vless_uuid}@{hop.server_host}:443"
            f"?encryption=none&security=tls&type=tcp#Chain-{_chain_tag(config_id)}"
        )
        files = [
            ("entry-config.json", json.dumps(xray_cfg, indent=2), 0o644),
        ]
        return f"{vless_uri}\n", service, files, bypass_meta

    def _deploy_xray_entry(
        self,
        runner: RemoteRunner,
        path: str,
        config_id: str,
        hop: HopSpec,
        downstream: ExitEndpoint | None,
        has_tor: bool,
    ) -> tuple[str, str]:
        mkdir_remote(runner, path)
        local_tor = has_tor
        client_conf, service, files, _bypass_meta = self._xray_entry_bundle(path, config_id, hop, downstream, local_tor)
        for name, content, mode in files:
            runner.upload_text(f"{path}/{name}", content, mode)
        compose = f"services:\n{service}\n"
        _ensure_compose_images(runner, compose)
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        podman_compose_up(runner, path, _compose_project_at(config_id, path))
        return client_conf, path

    def _deploy_ovpn_entry(
        self,
        runner: RemoteRunner,
        path: str,
        config_id: str,
        hop: HopSpec,
        downstream: ExitEndpoint | None,
        has_tor: bool,
    ) -> tuple[str, str]:
        transport = _ovpn_transport(hop)
        stub = (
            f"client\ndev tun\nproto {transport}\nremote {hop.server_host} 1194\n"
            f"nobind\npersist-key\npersist-tun\n"
        )
        return stub, path


def _hop_meta_for_storage(h: HopSpec, plan: ChainPlan) -> dict[str, Any]:
    hop: dict[str, Any] = {"id": h.id, "protocol": h.protocol, "serverId": h.server_id}
    if h.protocol == "openvpn":
        hop["transport"] = _ovpn_transport(h, plan=plan)
    if h.protocol == "xray" and h.xray_bypass:
        hop["xrayBypass"] = True
    return hop


def _chain_result_meta(plan: ChainPlan, client_config: str, *, config_id: str = "") -> dict[str, Any]:
    meta: dict[str, Any] = {
        "chain": True,
        "hops": [_hop_meta_for_storage(h, plan) for h in plan.hops],
        "exitHost": plan.exit.server_host,
        "torDynamicExit": plan.exit.protocol == "tor",
    }
    if plan.entry.protocol == "xray" and plan.entry.xray_bypass:
        meta["xrayBypass"] = True
    if plan.entry.protocol == "awg":
        tag = _chain_tag(config_id) if config_id else "chain"
        meta["hostAwg"] = True
        meta["listenPort"] = 55424
        meta["amneziaVpnUri"] = build_amnezia_awg_vpn_uri(
            client_config,
            host=plan.entry.server_host,
            port=55424,
            description=f"Hoplyra {tag}",
        )
    stripped = client_config.strip()
    if stripped.startswith("vless://"):
        meta["vlessUri"] = stripped.splitlines()[0].strip()
    elif "vless://" in stripped:
        for line in stripped.splitlines():
            if line.strip().startswith("vless://"):
                meta["vlessUri"] = line.strip()
                break
    return meta
