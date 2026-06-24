from __future__ import annotations

import json
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
from hoplyra.awg_params import build_awg_client_conf, build_awg_server_conf, generate_awg2_params
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
    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str) -> DeployResult:
        ...


class WireGuardDeployer(ProtocolDeployer):
    protocol = "wg"
    listen_port = 51820

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str) -> DeployResult:
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


class AmneziaWGDeployer(ProtocolDeployer):
    protocol = "awg"
    listen_port = 55424

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str) -> DeployResult:
        server_priv, server_pub = generate_wg_keypair()
        client_priv, client_pub = generate_wg_keypair()
        client_ip = "10.9.1.2"
        subnet = "10.9.1.0/24"
        awg = generate_awg2_params()
        psk = generate_wg_psk()

        awg_conf = build_awg_server_conf(
            server_priv=server_priv,
            client_pub=client_pub,
            client_ip=client_ip,
            listen_port=self.listen_port,
            post_up=nat_postup(subnet),
            post_down=nat_postdown(subnet),
            params=awg,
            preshared_key=psk,
        )
        client_conf = build_awg_client_conf(
            client_priv=client_priv,
            server_pub=server_pub,
            server_host=server_host,
            listen_port=self.listen_port,
            params=awg,
            preshared_key=psk,
        )
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-awg-{config_id[:8]}"
        conf_path = f"{path}/awg0.conf"
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
                "listenPort": self.listen_port,
                "hostAwg": True,
                "awgParams": awg.as_meta(),
                "amneziaVpnUri": build_amnezia_awg_vpn_uri(
                    client_conf,
                    host=server_host,
                    port=self.listen_port,
                    description=f"Hoplyra {config_id[:8]}",
                ),
            },
        )

    def stop(self, runner: RemoteRunner, config_id: str, instance_path: str) -> None:
        awg_quick_down(runner, f"{instance_path}/awg0.conf")
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

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str) -> DeployResult:
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


class XrayDeployer(ProtocolDeployer):
    protocol = "xray"
    listen_port = 443

    def __init__(self, *, bypass: bool = False) -> None:
        self.bypass = bypass

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str) -> DeployResult:
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-xray-{config_id[:8]}"

        if self.bypass:
            xray_config, client_text, meta, _secrets = format_bypass_client_bundle(
                server_host,
                config_id,
                port=self.listen_port,
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

        vless_uuid = str(uuid.uuid4())
        cert_pem, key_pem = tls_cert_paths()

        xray_config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "vless-in",
                    "port": self.listen_port,
                    "listen": "0.0.0.0",
                    "protocol": "vless",
                    "settings": {
                        "clients": [{"id": vless_uuid, "flow": "xtls-rprx-vision"}],
                        "decryption": "none",
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "tls",
                        "tlsSettings": {
                            "certificates": [
                                {"certificateFile": cert_pem, "keyFile": key_pem}
                            ],
                        },
                    },
                }
            ],
            "outbounds": [{"protocol": "freedom", "tag": "direct"}],
        }

        client_json = json.dumps(
            {
                "outbounds": [
                    {
                        "protocol": "vless",
                        "settings": {
                            "vnext": [
                                {
                                    "address": server_host,
                                    "port": self.listen_port,
                                    "users": [
                                        {
                                            "id": vless_uuid,
                                            "encryption": "none",
                                            "flow": "xtls-rprx-vision",
                                        }
                                    ],
                                }
                            ]
                        },
                        "streamSettings": {"network": "tcp", "security": "tls", "serverName": server_host},
                    }
                ]
            },
            indent=2,
        )

        vless_uri = (
            f"vless://{vless_uuid}@{server_host}:{self.listen_port}"
            f"?encryption=none&security=tls&type=tcp&flow=xtls-rprx-vision&sni={server_host}"
            f"#Hoplyra-{config_id[:8]}"
        )

        mkdir_remote(runner, path)
        ensure_xray_image(runner)
        runner.upload_text(f"{path}/config.json", json.dumps(xray_config, indent=2))
        compose = f"services:\n{compose_xray_service(container, path, tls_cn=server_host)}\n"
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        podman_compose_up(runner, path, self.compose_project(config_id))

        return DeployResult(
            client_config=f"{client_json}\n\n{vless_uri}\n",
            container_name=container,
            instance_path=path,
            meta={"vlessUuid": vless_uuid, "vlessUri": vless_uri, "listenPort": self.listen_port},
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
