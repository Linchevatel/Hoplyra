from __future__ import annotations

import json
import secrets
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from dataclasses import dataclass, field
from typing import Any

from hoplyra.db import DATA_DIR
from hoplyra.remote import RemoteRunner, mkdir_remote, nat_postdown, nat_postup, podman_compose_down, podman_compose_up
from hoplyra.container_images import _remote_has_image
from hoplyra.wg_keys import generate_wg_keypair, generate_wg_psk
from hoplyra.wg_runtime import compose_wg_service, ensure_wg_image
from hoplyra.xray_bypass import format_bypass_client_bundle
from hoplyra.xray_runtime import XRAY_MOUNT, compose_xray_service, ensure_xray_image, tls_cert_paths
from hoplyra.awg_runtime import awg_quick_down, awg_quick_up, ensure_awg_on_host
from hoplyra.awg_params import build_awg_client_conf, build_awg_server_conf, generate_awg_params, generate_awg2_params
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
    meta: dict[str, Any] = field(default_factory=dict)


class ProtocolDeployer(ABC):
    protocol: str = ""

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        raise NotImplementedError

    def compose_project(self, config_id: str) -> str:
        return f"cv-{self.protocol}-{config_id[:8]}"

    def stop(self, runner: RemoteRunner, config_id: str, instance_path: str) -> None:
        podman_compose_down(runner, instance_path, self.compose_project(config_id))


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


def _find_free_awg_port_and_subnet(runner: RemoteRunner) -> tuple[int, str, str, str]:
    code, out, _ = runner.run("ss -lun 2>/dev/null | grep -oE ':[0-9]+' | tr -d ':'")
    used_ports = set(int(p) for p in out.split() if p.isdigit())
    _, conf_out, _ = runner.run("grep -h 'ListenPort' /opt/hoplyra/instances/*/awg*.conf 2>/dev/null || true")
    for line in conf_out.splitlines():
        if "=" in line:
            val = line.split("=")[-1].strip()
            if val.isdigit():
                used_ports.add(int(val))

    port = 55424
    while port in used_ports:
        port += 1

    subnet_idx = (port - 55400) % 250
    if subnet_idx < 1:
        subnet_idx = 1
    subnet = f"10.9.{subnet_idx}.0/24"
    server_ip = f"10.9.{subnet_idx}.1/24"
    client_ip = f"10.9.{subnet_idx}.2"
    return port, subnet, server_ip, client_ip


class AmneziaWGDeployer(ProtocolDeployer):
    protocol = "awg"
    listen_port = 55424

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, awg_version: str = "awg2.0", **kwargs: Any) -> DeployResult:
        server_priv, server_pub = generate_wg_keypair()
        client_priv, client_pub = generate_wg_keypair()
        port, subnet, server_ip, client_ip = _find_free_awg_port_and_subnet(runner)
        awg = generate_awg_params(version=awg_version)
        psk = generate_wg_psk()

        awg_conf = build_awg_server_conf(
            server_priv=server_priv,
            client_pub=client_pub,
            client_ip=client_ip,
            listen_port=port,
            post_up=nat_postup(subnet),
            post_down=nat_postdown(subnet),
            params=awg,
            preshared_key=psk,
            server_ip=server_ip,
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
        tag = config_id[:8]
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-awg-{tag}"
        conf_path = f"{path}/awg_{tag}.conf"
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
                "awgParams": awg.as_meta(),
                "amneziaVpnUri": build_amnezia_awg_vpn_uri(
                    client_conf,
                    host=server_host,
                    port=port,
                    description=f"Hoplyra {tag}",
                ),
            },
        )

    def stop(self, runner: RemoteRunner, config_id: str, instance_path: str) -> None:
        tag = config_id[:8]
        conf_path = f"{instance_path}/awg_{tag}.conf"
        awg_quick_down(runner, conf_path)
        runner.run(f"find {instance_path} -name 'awg*.conf' -exec awg-quick down {{}} \\; 2>/dev/null || true")
        runner.run(f"ip link delete awg_{tag} 2>/dev/null || true")
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


HY2_IMAGE = "docker.io/teddysun/hysteria:latest"


def ensure_hy2_image(runner: RemoteRunner) -> None:
    """Pull Hysteria 2 image only if not already present on the remote host."""
    if _remote_has_image(runner, HY2_IMAGE):
        return
    code, _, err = runner.run(f"podman pull {HY2_IMAGE}", timeout=600)
    if code != 0:
        raise RuntimeError(f"Failed to pull {HY2_IMAGE}: {err[:300]}")


class Hysteria2Deployer(ProtocolDeployer):
    protocol = "hysteria2"
    listen_port = 443

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str) -> DeployResult:
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-hy2-{config_id[:8]}"
        password = secrets.token_urlsafe(16)
        obfs_password = secrets.token_urlsafe(16)

        mkdir_remote(runner, path)
        runner.run(
            f"openssl req -x509 -newkey rsa:2048 -nodes "
            f"-keyout {path}/key.pem -out {path}/cert.pem -days 365 "
            f"-subj '/CN=bing.com' -addext 'subjectAltName=DNS:bing.com'"
        )

        config_yaml = f"""listen: :{self.listen_port}
tls:
  cert: /etc/hysteria/cert.pem
  key: /etc/hysteria/key.pem
obfs:
  type: salamander
  salamander:
    password: {obfs_password}
auth:
  type: password
  password: {password}
ignoreClientBandwidth: true
masquerade:
  type: proxy
  proxy:
    url: https://bing.com
    rewriteHost: true
"""
        runner.upload_text(f"{path}/config.yaml", config_yaml)

        ensure_hy2_image(runner)

        compose = f"""services:
  hy2:
    image: {HY2_IMAGE}
    container_name: {container}
    network_mode: host
    command: ["hysteria", "server", "-c", "/etc/hysteria/config.yaml"]
    volumes:
      - {path}:/etc/hysteria
    restart: unless-stopped
"""
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        podman_compose_up(runner, path, self.compose_project(config_id))

        runner.run(
            "iptables -t nat -A PREROUTING -p udp --dport 20000:50000 -j REDIRECT --to-ports 443 2>/dev/null || true"
        )

        hy2_uri = (
            f"hysteria2://{password}@{server_host}:{self.listen_port}"
            f"?mport=20000-50000,443&obfs=salamander&obfs-password={obfs_password}&insecure=1&sni=bing.com"
            f"#Hoplyra-{config_id[:8]}"
        )

        return DeployResult(
            client_config=f"{hy2_uri}\n",
            container_name=container,
            instance_path=path,
            meta={"password": password, "hy2Uri": hy2_uri, "listenPort": self.listen_port},
        )


SINGBOX_IMAGE = "ghcr.io/sagernet/sing-box:latest"


def ensure_singbox_image(runner: RemoteRunner) -> None:
    """Pull sing-box image only if not already present on the remote host."""
    if _remote_has_image(runner, SINGBOX_IMAGE):
        return
    code, _, err = runner.run(f"podman pull {SINGBOX_IMAGE}", timeout=600)
    if code != 0:
        raise RuntimeError(f"Failed to pull {SINGBOX_IMAGE}: {err[:300]}")


class TuicDeployer(ProtocolDeployer):
    protocol = "tuic"
    listen_port = 8448

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str) -> DeployResult:
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-tuic-{config_id[:8]}"
        tuic_uuid = str(uuid.uuid4())
        tuic_pass = secrets.token_urlsafe(16)

        mkdir_remote(runner, path)

        # Generate self-signed TLS cert with SAN (required by modern Go/TLS)
        runner.run(
            f"openssl req -x509 -newkey rsa:2048 -nodes "
            f"-keyout {path}/key.pem -out {path}/cert.pem -days 365 "
            f"-subj '/CN=bing.com' -addext 'subjectAltName=DNS:bing.com'"
        )

        singbox_config = {
            "log": {"level": "warn"},
            "inbounds": [
                {
                    "type": "tuic",
                    "tag": "tuic-in",
                    "listen": "::",
                    "listen_port": self.listen_port,
                    "users": [{"uuid": tuic_uuid, "password": tuic_pass}],
                    "congestion_control": "bbr",
                    "tls": {
                        "enabled": True,
                        "alpn": ["h3"],
                        "certificate_path": "/etc/tuic/cert.pem",
                        "key_path": "/etc/tuic/key.pem",
                    },
                }
            ],
            "outbounds": [{"type": "direct", "tag": "direct"}],
        }

        tuic_uri = (
            f"tuic://{tuic_uuid}:{tuic_pass}@{server_host}:{self.listen_port}"
            f"?congestion_control=bbr&alpn=h3&sni=bing.com&insecure=1"
            f"#Hoplyra-{config_id[:8]}"
        )

        runner.upload_text(f"{path}/config.json", json.dumps(singbox_config, indent=2))

        ensure_singbox_image(runner)

        compose = f"""services:
  tuic:
    image: {SINGBOX_IMAGE}
    container_name: {container}
    network_mode: host
    command: ["run", "-c", "/etc/tuic/config.json"]
    volumes:
      - {path}:/etc/tuic
    restart: unless-stopped
"""
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        podman_compose_up(runner, path, self.compose_project(config_id))

        return DeployResult(
            client_config=f"{tuic_uri}\n",
            container_name=container,
            instance_path=path,
            meta={"tuicUuid": tuic_uuid, "password": tuic_pass, "tuicUri": tuic_uri, "listenPort": self.listen_port},
        )





DEPLOYERS: dict[str, ProtocolDeployer] = {
    "wg": WireGuardDeployer(),
    "awg": AmneziaWGDeployer(),
    "xray": XrayDeployer(),
    "hysteria2": Hysteria2Deployer(),
    "tuic": TuicDeployer(),
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
