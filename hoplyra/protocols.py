from __future__ import annotations

import json
import re
import secrets
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from hoplyra.db import DATA_DIR
from hoplyra.remote import RemoteRunner, mkdir_remote, nat_postdown, nat_postup, podman_compose_down, podman_compose_up
from hoplyra.wg_keys import generate_wg_keypair, generate_wg_psk
from hoplyra.wg_runtime import compose_wg_service, ensure_wg_image
from hoplyra.xray_bypass import format_bypass_client_bundle
from hoplyra.xray_runtime import XRAY_MOUNT, compose_xray_service, ensure_xray_image, tls_cert_paths
from hoplyra.awg_runtime import awg_quick_down, awg_quick_up, ensure_awg_on_host
from hoplyra.awg_params import (
    build_awg_client_conf,
    build_awg_server_conf,
    generate_awg1_0_params,
    generate_awg1_5_params,
    generate_awg2_params,
    generate_awg3_1_params,
    generate_awg_params,
)
from hoplyra.amnezia_export import build_amnezia_awg_vpn_uri
from hoplyra.openvpn_runtime import (
    build_client_ovpn,
    compose_service,
    ensure_openvpn_image,
    ensure_openvpn_port_free,
    provision_openvpn_instance,
    server_conf_content,
)


def _instance_dir(config_id: str, local: bool) -> str:
    if local:
        return str(DATA_DIR / "instances" / config_id)
    return f"/opt/hoplyra/instances/{config_id}"


def default_instance_path(config_id: str, *, is_local: bool) -> str:
    return _instance_dir(config_id, is_local)


@dataclass
class DeployResult:
    client_config: str
    container_name: str
    instance_path: str
    meta: dict[str, Any]


class ProtocolDeployer(ABC):
    protocol: str
    listen_port: int

    def compose_project(self, config_id: str) -> str:
        return f"cv-{self.protocol}-{config_id[:8]}"

    def stop(self, runner: RemoteRunner, config_id: str, instance_path: str) -> None:
        podman_compose_down(runner, instance_path, self.compose_project(config_id))

    @abstractmethod
    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        ...


class WireGuardDeployer(ProtocolDeployer):
    protocol = "wg"
    listen_port = 51820

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        server_priv, server_pub = generate_wg_keypair()
        client_priv, client_pub = generate_wg_keypair()
        client_ip = "10.66.66.2"
        subnet = "10.66.66.0/24"

        wg_conf = f"""[Interface]
Address = 10.66.66.1/24
ListenPort = {self.listen_port}
PrivateKey = {server_priv}
PostUp = {nat_postup(subnet)}
PostDown = {nat_postdown(subnet)}

[Peer]
PublicKey = {client_pub}
AllowedIPs = {client_ip}/32
"""

        client_conf = f"""[Interface]
PrivateKey = {client_priv}
Address = {client_ip}/32
DNS = 1.1.1.1, 8.8.8.8

[Peer]
PublicKey = {server_pub}
Endpoint = {server_host}:{self.listen_port}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-wg-{config_id[:8]}"
        conf_path = f"{path}/wg0.conf"
        ensure_wg_image(runner)
        mkdir_remote(runner, path)
        runner.upload_text(conf_path, wg_conf, 0o600)
        compose = f"services:\n{compose_wg_service(container, conf_path, cap_sys_module=True)}\n"
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        podman_compose_up(runner, path, self.compose_project(config_id))
        return DeployResult(
            client_config=client_conf,
            container_name=container,
            instance_path=path,
            meta={"serverPublicKey": server_pub, "listenPort": self.listen_port},
        )


def _find_free_udp_port(runner: RemoteRunner, base_port: int = 55424) -> int:
    used: set[int] = set()
    _, out1, _ = runner.run("ss -ulpn 2>/dev/null", timeout=30)
    for line in out1.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3]
            if ":" in addr:
                try:
                    used.add(int(addr.rsplit(":", 1)[-1]))
                except ValueError:
                    pass
    _, out2, _ = runner.run("awg show 2>/dev/null | grep 'listening port'", timeout=30)
    for line in out2.splitlines():
        match = re.search(r"listening port:\s*(\d+)", line)
        if match:
            used.add(int(match.group(1)))
    _, out3, _ = runner.run("wg show 2>/dev/null | grep 'listening port'", timeout=30)
    for line in out3.splitlines():
        match = re.search(r"listening port:\s*(\d+)", line)
        if match:
            used.add(int(match.group(1)))
    port = base_port
    while port in used:
        port += 1
    return port


def _find_free_awg_subnet(runner: RemoteRunner) -> tuple[str, str, str]:
    code, out, _ = runner.run("ip addr show 2>/dev/null", timeout=30)
    used: set[int] = set()
    for line in out.splitlines():
        if "10.9." in line:
            match = re.search(r"10\.9\.(\d+)\.", line)
            if match:
                used.add(int(match.group(1)))
    idx = 1
    while idx in used:
        idx += 1
    return f"10.9.{idx}.1/24", f"10.9.{idx}.2", f"10.9.{idx}.0/24"


class AmneziaWGDeployer(ProtocolDeployer):
    protocol = "awg"
    listen_port = 55424

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        awg_ver_raw = kwargs.get("awg_version") or kwargs.get("awgVersion") or "awg3.1"
        awg_ver_clean = str(awg_ver_raw).lower().strip()
        if awg_ver_clean in ("awg3.1", "3.1", "3"):
            awg = generate_awg3_1_params()
            ver_label = "awg3.1"
        elif awg_ver_clean in ("awg1.0", "1.0", "1", "awg"):
            awg = generate_awg1_0_params()
            ver_label = "awg1.0"
        elif awg_ver_clean in ("awg1.5", "1.5"):
            awg = generate_awg1_5_params()
            ver_label = "awg1.5"
        else:
            awg = generate_awg2_params()
            ver_label = "awg2.0"

        port = _find_free_udp_port(runner, self.listen_port)
        server_ip_cidr, client_ip, subnet = _find_free_awg_subnet(runner)

        server_priv, server_pub = generate_wg_keypair()
        client_priv, client_pub = generate_wg_keypair()
        psk = generate_wg_psk()

        awg_conf = build_awg_server_conf(
            server_priv=server_priv,
            client_pub=client_pub,
            client_ip=client_ip,
            listen_port=port,
            server_ip_cidr=server_ip_cidr,
            post_up=nat_postup(subnet),
            post_down=nat_postdown(subnet),
            params=awg,
            preshared_key=psk,
        )
        client_conf = build_awg_client_conf(
            client_priv=client_priv,
            server_pub=server_pub,
            server_host=server_host,
            listen_port=port,
            client_ip=f"{client_ip}/32",
            params=awg,
            preshared_key=psk,
        )
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-awg-{config_id[:8]}"
        conf_name = f"awg_{config_id[:8]}.conf"
        conf_path = f"{path}/{conf_name}"
        mkdir_remote(runner, path)
        runner.upload_text(conf_path, awg_conf, 0o600)
        ensure_awg_on_host(runner)
        awg_quick_up(runner, conf_path)
        return DeployResult(
            client_config=client_conf,
            container_name=container,
            instance_path=path,
            meta={
                "serverPublicKey": server_pub,
                "listenPort": port,
                "hostAwg": True,
                "awgVersion": ver_label,
                "awgParams": awg.as_meta(),
                "amneziaVpnUri": build_amnezia_awg_vpn_uri(
                    client_conf,
                    host=server_host,
                    port=port,
                    description=f"Hoplyra {config_id[:8]} ({ver_label})",
                    awg_version=ver_label,
                ),
            },
        )

    def stop(self, runner: RemoteRunner, config_id: str, instance_path: str) -> None:
        conf_name = f"awg_{config_id[:8]}.conf"
        awg_quick_down(runner, f"{instance_path}/{conf_name}")
        awg_quick_down(runner, f"{instance_path}/awg0.conf")
        runner.run(f"ip link delete awg_{config_id[:8]} 2>/dev/null || true; ip link delete awg_{config_id.replace('-', '')[:12]} 2>/dev/null || true", timeout=30)
        runner.run(f"rm -rf {instance_path}")


class OpenVPNDeployer(ProtocolDeployer):
    protocol = "openvpn"
    listen_port = 1194

    def __init__(self, transport: str = "udp") -> None:
        t = transport.lower()
        if t not in ("udp", "tcp"):
            raise ValueError(f"OpenVPN transport must be udp or tcp, got {transport!r}")
        self.transport = t

    def _build_client_ovpn(self, runner: RemoteRunner, path: str, server_host: str) -> str:
        return build_client_ovpn(runner, path, server_host, self.transport, self.listen_port)

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-openvpn-{config_id[:8]}"

        ensure_openvpn_image(runner)
        mkdir_remote(runner, path)
        runner.upload_text(f"{path}/server.conf", server_conf_content(self.transport, self.listen_port))

        compose = f"services:\n{compose_service(container, path)}\n"
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        ensure_openvpn_port_free(runner)
        podman_compose_up(runner, path, self.compose_project(config_id))

        provision_openvpn_instance(runner, path, container, self.transport, listen_port=self.listen_port)

        client_conf = self._build_client_ovpn(runner, path, server_host)
        return DeployResult(
            client_config=client_conf,
            container_name=container,
            instance_path=path,
            meta={"listenPort": self.listen_port, "transport": self.transport},
        )


def _find_free_tcp_port(runner: RemoteRunner, base_port: int = 443) -> int:
    used: set[int] = set()
    _, out, _ = runner.run("ss -tlpn 2>/dev/null", timeout=30)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3]
            if ":" in addr:
                try:
                    used.add(int(addr.rsplit(":", 1)[-1]))
                except ValueError:
                    pass
    port = base_port
    while port in used:
        port += 1
    return port


class XrayDeployer(ProtocolDeployer):
    protocol = "xray"
    listen_port = 443

    def __init__(self, *, bypass: bool = False) -> None:
        self.bypass = bypass

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-xray-{config_id[:8]}"

        port = _find_free_tcp_port(runner, self.listen_port)
        xray_config, client_text, meta, _secrets = format_bypass_client_bundle(
            server_host,
            config_id,
            port=port,
        )
        mkdir_remote(runner, path)
        ensure_xray_image(runner)
        runner.upload_text(f"{path}/config.json", json.dumps(xray_config, indent=2))
        compose = f"services:\n{compose_xray_service(container, path)}\n"
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        podman_compose_up(runner, path, self.compose_project(config_id))
        return DeployResult(
            client_config=client_text,
            container_name=container,
            instance_path=path,
            meta=meta,
        )


DEPLOYERS: dict[str, ProtocolDeployer] = {
    "wg": WireGuardDeployer(),
    "awg": AmneziaWGDeployer(),
    "xray": XrayDeployer(),
}


def _openvpn_transport(meta: dict[str, Any] | None) -> str:
    t = (meta or {}).get("transport", "udp")
    return t if t in ("udp", "tcp") else "udp"


def get_deployer(protocol: str, *, transport: str | None = None, xray_bypass: bool = False) -> ProtocolDeployer:
    if protocol == "openvpn":
        return OpenVPNDeployer(transport=transport or "udp")
    if protocol == "xray":
        return XrayDeployer(bypass=xray_bypass)
    if protocol not in DEPLOYERS:
        raise ValueError(f"Unsupported protocol: {protocol}")
    return DEPLOYERS[protocol]


def get_deployer_for_config(protocol: str, meta: dict[str, Any] | None = None) -> ProtocolDeployer:
    if protocol == "openvpn":
        return OpenVPNDeployer(transport=_openvpn_transport(meta))
    if protocol == "xray":
        return XrayDeployer(bypass=bool((meta or {}).get("xrayBypass")))
    return get_deployer(protocol)
