from __future__ import annotations

from hoplyra.hosts import endpoint_host

import json
import re
import secrets
import shlex
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from hoplyra.container_images import ensure_image
from hoplyra.db import DATA_DIR
from hoplyra.deploy_log import publish
from hoplyra.remote import RemoteRunner, mkdir_remote, nat_postdown, nat_postup, podman_compose_down, podman_compose_up
from hoplyra.wg_keys import generate_wg_keypair, generate_wg_psk
from hoplyra.wg_runtime import compose_wg_service, ensure_wg_image
from hoplyra.xray_bypass import format_bypass_client_bundle
from hoplyra.xray_runtime import XRAY_MOUNT, compose_xray_service, ensure_xray_image, tls_cert_paths
from hoplyra.awg_runtime import awg_quick_down, awg_quick_up, compose_awg_service, ensure_awg_image, ensure_awg_on_host
from hoplyra.awg_params import (
    AwgObfuscationParams,
    build_awg_client_conf,
    build_awg_server_conf,
    check_awg_params_valid,
    generate_auto_awg_params,
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

    def stop(self, runner: RemoteRunner, config_id: str, instance_path: str, *, runtime: str | None = None, **kwargs: Any) -> None:
        if runtime:
            podman_compose_down(runner, instance_path, self.compose_project(config_id), strict=True, runtime=runtime)
        else:
            podman_compose_down(runner, instance_path, self.compose_project(config_id))

    @abstractmethod
    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        ...


class WireGuardDeployer(ProtocolDeployer):
    protocol = "wg"
    listen_port = 51820

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        publish(config_id, "Generating WireGuard keys and configuration", stage="config")
        server_priv, server_pub = generate_wg_keypair()
        client_priv, client_pub = generate_wg_keypair()
        resources = kwargs.get("resources") or {}
        publish(config_id, "Searching for free UDP listen port and IP subnet", stage="network")
        port = resources.get("listenPort") or _find_free_udp_port(runner, self.listen_port)
        subnet = resources.get("subnet") or "10.66.66.0/24"
        publish(config_id, f"Allocated port {port}, subnet {subnet}", stage="network")
        prefix = subnet.rsplit(".", 1)[0]
        client_ip = f"{prefix}.2"
        interface = f"wg_{config_id[:8]}"

        wg_conf = f"""[Interface]
Address = {prefix}.1/24
ListenPort = {port}
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
Endpoint = {endpoint_host(server_host)}:{port}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 15
"""
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-wg-{config_id[:8]}"
        conf_path = f"{path}/{interface}.conf"
        publish(config_id, "Preparing WireGuard container image", stage="image")
        ensure_wg_image(runner)
        mkdir_remote(runner, path)
        runner.upload_text(conf_path, wg_conf, 0o600)
        compose = f"services:\n{compose_wg_service(container, conf_path, cap_sys_module=True, interface=interface)}\n"
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        publish(config_id, f"Starting WireGuard container {container}", stage="compose")
        podman_compose_up(runner, path, self.compose_project(config_id))
        publish(config_id, f"WireGuard container {container} is running on UDP port {port}", stage="verify", level="success")
        return DeployResult(
            client_config=client_conf,
            container_name=container,
            instance_path=path,
            meta={"serverPublicKey": server_pub, "listenPort": port, "subnet": subnet, "interface": interface},
        )


def _find_free_udp_port(runner: RemoteRunner, base_port: int = 55424) -> int:
    used: set[int] = set()
    _, out1, _ = runner.run("ss -ulpn 2>/dev/null", timeout=30, silent=True)
    for line in out1.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3]
            if ":" in addr:
                try:
                    used.add(int(addr.rsplit(":", 1)[-1]))
                except ValueError:
                    pass
    _, out2, _ = runner.run("awg show 2>/dev/null | grep 'listening port'", timeout=30, silent=True)
    for line in out2.splitlines():
        match = re.search(r"listening port:\s*(\d+)", line)
        if match:
            used.add(int(match.group(1)))
    _, out3, _ = runner.run("wg show 2>/dev/null | grep 'listening port'", timeout=30, silent=True)
    for line in out3.splitlines():
        match = re.search(r"listening port:\s*(\d+)", line)
        if match:
            used.add(int(match.group(1)))
    _, links, _ = runner.run("ip -o link show 2>/dev/null", timeout=30, silent=True)
    for name in re.findall(r"^\d+:\s+((?:awg|wg)[\w.-]*)(?:@[^:]+)?:", links, re.M):
        for tool in ("awg", "wg"):
            _, output, _ = runner.run(
                f"{tool} show {shlex.quote(name)} listen-port 2>/dev/null", timeout=30, silent=True
            )
            if output.strip().isdigit():
                used.add(int(output.strip()))
    for port in range(base_port, 65536):
        if port not in used:
            return port
    raise RuntimeError("No free UDP listen port available")


def _find_free_awg_subnet(runner: RemoteRunner) -> tuple[str, str, str]:
    code, out, _ = runner.run("ip addr show 2>/dev/null", timeout=30)
    used: set[int] = set()
    for line in out.splitlines():
        if "10.9." in line:
            match = re.search(r"10\.9\.(\d+)\.", line)
            if match:
                used.add(int(match.group(1)))
    for idx in range(1, 255):
        if idx not in used:
            return f"10.9.{idx}.1/24", f"10.9.{idx}.2", f"10.9.{idx}.0/24"
    raise RuntimeError("No free AWG /24 subnet available")


class AmneziaWGDeployer(ProtocolDeployer):
    protocol = "awg"
    listen_port = 55424

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        awg_ver_raw = kwargs.get("awg_version") or kwargs.get("awgVersion") or "awg3.1"
        awg_ver_clean = str(awg_ver_raw).lower().strip()
        custom_params = kwargs.get("awg_params") or kwargs.get("awgParams")

        if awg_ver_clean in ("awg3.1", "3.1", "3"):
            ver_label = "awg3.1"
        elif awg_ver_clean in ("awg1.0", "1.0", "1", "awg"):
            ver_label = "awg1.0"
        elif awg_ver_clean in ("awg1.5", "1.5"):
            ver_label = "awg1.5"
        else:
            ver_label = "awg2.0"

        publish(config_id, f"Generating AmneziaWG ({ver_label}) keys and parameters", stage="config")

        if isinstance(custom_params, AwgObfuscationParams):
            awg = custom_params
        elif isinstance(custom_params, dict):
            awg = generate_auto_awg_params(ver_label, overrides=custom_params)
        else:
            awg = generate_auto_awg_params(ver_label)

        check_awg_params_valid(awg, awg_version=ver_label)

        resources = kwargs.get("resources") or {}
        publish(config_id, "Searching for free UDP listen port and IP subnet", stage="network")
        port = resources.get("listenPort") or _find_free_udp_port(runner, self.listen_port)
        server_ip_cidr, client_ip, subnet = _find_free_awg_subnet(runner)
        if resources.get("subnet"):
            subnet = resources["subnet"]
            prefix = subnet.rsplit(".", 1)[0]
            server_ip_cidr, client_ip = f"{prefix}.1/24", f"{prefix}.2"
        publish(config_id, f"Allocated port {port}, subnet {subnet}", stage="network")

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
        interface = f"awg_{config_id[:8]}"
        conf_name = f"{interface}.conf"
        conf_path = f"{path}/{conf_name}"
        def cleanup(*, remove_dir: bool) -> None:
            # Clean up both container and any host link
            commands = [
                f"podman rm -f {shlex.quote(container)} 2>/dev/null || docker rm -f {shlex.quote(container)} 2>/dev/null || true",
                f"awg-quick down {shlex.quote(conf_path)} 2>/dev/null || true",
                f"ip link delete {shlex.quote(interface)} 2>/dev/null || true",
            ]
            if remove_dir:
                commands.append(f"rm -rf -- {shlex.quote(path)}")
            for command in commands:
                try:
                    runner.run(command, timeout=60)
                except Exception:
                    pass

        try:
            mkdir_remote(runner, path)

            # 1. First probe if the kernel module is available on the system
            publish(config_id, "Probing AmneziaWG kernel module on host", stage="awg")
            c_probe, _, _ = runner.run("modprobe amneziawg 2>/dev/null && echo ok")
            if c_probe == 0:
                try:
                    ensure_awg_on_host(runner)
                except Exception:
                    pass

            c_mod, _, _ = runner.run("modprobe amneziawg 2>/dev/null && command -v awg-quick >/dev/null && echo ok")
            use_host_kernel = (c_mod == 0)

            publish(config_id, "Starting AmneziaWG (native host kernel)" if use_host_kernel else "Starting AmneziaWG (container mode)", stage="awg")
            if use_host_kernel:
                runner.upload_text(conf_path, awg_conf, 0o600)
                try:
                    awg_quick_up(runner, conf_path)
                except RuntimeError:
                    cleanup(remove_dir=False)
                    # Only retry if another listener has occupied the failed port.
                    if _find_free_udp_port(runner, port) == port:
                        raise
                    new_port = _find_free_udp_port(runner, port + 1)
                    awg_conf = awg_conf.replace(f"ListenPort = {port}", f"ListenPort = {new_port}")
                    client_conf = client_conf.replace(
                        f"Endpoint = {endpoint_host(server_host)}:{port}",
                        f"Endpoint = {endpoint_host(server_host)}:{new_port}",
                    )
                    port = new_port
                    runner.upload_text(conf_path, awg_conf, 0o600)
                    awg_quick_up(runner, conf_path)
                publish(config_id, f"AmneziaWG interface {interface} is UP on UDP port {port}", stage="verify", level="success")
            else:
                # Always in container via hoplyra-awg:1
                compose = f"services:\n{compose_awg_service(container, conf_path, interface=interface)}\n"
                runner.upload_text(f"{path}/docker-compose.yml", compose)
                runner.upload_text(conf_path, awg_conf, 0o600)
                publish(config_id, "Preparing AmneziaWG container image", stage="image")
                ensure_awg_image(runner)
                try:
                    publish(config_id, f"Starting AmneziaWG container {container}", stage="compose")
                    podman_compose_up(runner, path, self.compose_project(config_id))
                except RuntimeError:
                    cleanup(remove_dir=False)
                    if _find_free_udp_port(runner, port) == port:
                        raise
                    new_port = _find_free_udp_port(runner, port + 1)
                    awg_conf = awg_conf.replace(f"ListenPort = {port}", f"ListenPort = {new_port}")
                    client_conf = client_conf.replace(
                        f"Endpoint = {endpoint_host(server_host)}:{port}",
                        f"Endpoint = {endpoint_host(server_host)}:{new_port}",
                    )
                    port = new_port
                    compose = f"services:\n{compose_awg_service(container, conf_path, interface=interface)}\n"
                    runner.upload_text(f"{path}/docker-compose.yml", compose)
                    runner.upload_text(conf_path, awg_conf, 0o600)
                    publish(config_id, f"Starting AmneziaWG container {container}", stage="compose")
                    podman_compose_up(runner, path, self.compose_project(config_id))
                publish(config_id, f"AmneziaWG container {container} is running on UDP port {port}", stage="verify", level="success")
        except Exception:
            cleanup(remove_dir=True)
            raise
        return DeployResult(
            client_config=client_conf,
            container_name=container,
            instance_path=path,
            meta={
                "serverPublicKey": server_pub,
                "listenPort": port,
                "hostAwg": use_host_kernel,
                "interface": interface,
                "subnet": subnet,
                "awgVersion": ver_label,
                "awgParams": awg.as_meta(),
                "awgMode": awg.obfuscation_mode,
                "amneziaVpnUri": build_amnezia_awg_vpn_uri(
                    client_conf,
                    host=server_host,
                    port=port,
                    description=f"Hoplyra {config_id[:8]} ({ver_label})",
                    awg_version=ver_label,
                ),
            },
        )

    def stop(self, runner: RemoteRunner, config_id: str, instance_path: str, *, runtime: str | None = None, **kwargs: Any) -> None:
        container = f"cv-awg-{config_id[:8]}"
        conf_name = f"awg_{config_id[:8]}.conf"
        podman_compose_down(runner, instance_path, self.compose_project(config_id), timeout=30, runtime=runtime)
        runner.run(
            f"podman rm -f {shlex.quote(container)} 2>/dev/null || docker rm -f {shlex.quote(container)} 2>/dev/null || true; "
            f"awg-quick down {shlex.quote(instance_path)}/{conf_name} 2>/dev/null || true; "
            f"awg-quick down {shlex.quote(instance_path)}/awg0.conf 2>/dev/null || true; "
            f"ip link delete {shlex.quote(conf_name[:-5])} 2>/dev/null || true; "
            f"ip link delete awg_{config_id.replace('-', '')[:12]} 2>/dev/null || true",
            timeout=30,
        )
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
        publish(config_id, f"Generating OpenVPN ({self.transport.upper()}) configuration and certificates", stage="config")
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-openvpn-{config_id[:8]}"

        resources = kwargs.get("resources") or {}
        publish(config_id, f"Searching for free port for OpenVPN {self.transport.upper()}", stage="network")
        self.listen_port = resources.get("listenPort") or _find_free_udp_port(runner, self.listen_port)
        subnet = resources.get("subnet", "10.8.0.0/24")
        interface = f"tun_{config_id[:8]}"
        publish(config_id, f"Allocated port {self.listen_port}/{self.transport}, subnet {subnet}", stage="network")
        publish(config_id, "Preparing OpenVPN container image", stage="image")
        ensure_openvpn_image(runner)
        mkdir_remote(runner, path)
        runner.upload_text(f"{path}/server.conf", server_conf_content(self.transport, self.listen_port).replace("dev tun\n", f"dev {interface}\ndev-type tun\n").replace("server 10.8.0.0", f"server {subnet.partition('/')[0]}"))

        compose = f"services:\n{compose_service(container, path, subnet=subnet)}\n"
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        publish(config_id, f"Starting OpenVPN container {container}", stage="compose")
        podman_compose_up(runner, path, self.compose_project(config_id))

        provision_openvpn_instance(runner, path, container, self.transport, listen_port=self.listen_port)
        publish(config_id, f"OpenVPN service is running on port {self.listen_port}/{self.transport}", stage="verify", level="success")

        client_conf = self._build_client_ovpn(runner, path, server_host)
        return DeployResult(
            client_config=client_conf,
            container_name=container,
            instance_path=path,
            meta={"listenPort": self.listen_port, "transport": self.transport, "subnet": subnet, "interface": interface},
        )


def _find_free_tcp_port(runner: RemoteRunner, base_port: int = 443) -> int:
    used: set[int] = set()
    _, out, _ = runner.run("ss -tlpn 2>/dev/null", timeout=30, silent=True)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3]
            if ":" in addr:
                try:
                    used.add(int(addr.rsplit(":", 1)[-1]))
                except ValueError:
                    pass
    _, links, _ = runner.run("ip -o link show 2>/dev/null", timeout=30, silent=True)
    for name in re.findall(r"^\d+:\s+((?:awg|wg)[\w.-]*)(?:@[^:]+)?:", links, re.M):
        for tool in ("awg", "wg"):
            _, output, _ = runner.run(
                f"{tool} show {shlex.quote(name)} listen-port 2>/dev/null", timeout=30, silent=True
            )
            if output.strip().isdigit():
                used.add(int(output.strip()))
    for port in range(base_port, 449):
        if port not in used:
            return port
    raise RuntimeError("No free Xray ports available in range 443-448")


class XrayDeployer(ProtocolDeployer):
    protocol = "xray"
    listen_port = 443

    def __init__(self, *, bypass: bool = False) -> None:
        self.bypass = bypass

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        publish(config_id, "Generating Xray VLESS Reality configuration and keypair", stage="config")
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-xray-{config_id[:8]}"

        publish(config_id, "Searching for free TCP port in 443-448 pool", stage="network")
        port = (kwargs.get("resources") or {}).get("listenPort") or _find_free_tcp_port(runner, self.listen_port)
        publish(config_id, f"Allocated listening port {port}/tcp", stage="network")
        xray_config, client_text, meta, _secrets = format_bypass_client_bundle(
            server_host,
            config_id,
            port=port,
        )
        mkdir_remote(runner, path)
        publish(config_id, "Preparing Xray container image", stage="image")
        ensure_xray_image(runner)
        runner.upload_text(f"{path}/config.json", json.dumps(xray_config, indent=2))
        compose = f"services:\n{compose_xray_service(container, path)}\n"
        runner.upload_text(f"{path}/docker-compose.yml", compose)
        publish(config_id, f"Starting Xray container {container}", stage="compose")
        podman_compose_up(runner, path, self.compose_project(config_id))
        publish(config_id, f"Xray container {container} is running on port {port}/tcp", stage="verify", level="success")
        return DeployResult(
            client_config=client_text,
            container_name=container,
            instance_path=path,
            meta=meta,
        )



def find_hysteria2_port(runner: RemoteRunner, base_port: int = 443) -> int:
    # Avoid both UDP and TCP reservations so protocols can share the allocator.
    code, output, err = runner.run("ss -H -lntu", timeout=30)
    if code != 0:
        raise RuntimeError(f"Не удалось получить список занятых портов через ss: {err or output or 'exit ' + str(code)}")
    used = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 5:
            match = re.search(r":(\d+)$", fields[4])
            if match:
                used.add(int(match[1]))
    for port in range(base_port, 65536):
        if port not in used:
            return port
    raise RuntimeError("No free Hysteria 2 listen port available")


def generate_self_signed_cert_bundle(san: str = "bing.com") -> tuple[str, str, str]:
    """Generate self-signed ECDSA P-256 certificate with SAN and SHA-256 fingerprint for pinSHA256."""
    from datetime import datetime, timezone, timedelta
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import hashlib

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    pin_sha256 = hashlib.sha256(cert_der).hexdigest()
    return cert_pem, key_pem, pin_sha256


def hysteria2_bundle(path: str, container: str, host: str, port: int, *,
                     host_network: bool = False, socks_port: int | None = None,
                     service: str = "hysteria2",
                     cert_bundle: tuple[str, str, str] | None = None) -> tuple[str, str, str, dict[str, Any], tuple[str, str]]:
    """Build server YAML, compose service, importable client material, metadata, and cert files (cert_pem, key_pem)."""
    password = secrets.token_urlsafe(16)
    if cert_bundle is None:
        cert_pem, key_pem, pin_sha256 = generate_self_signed_cert_bundle("bing.com")
    else:
        cert_pem, key_pem, pin_sha256 = cert_bundle

    config = f"""listen: :{port}
tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key
auth:
  type: userpass
  userpass:
    primary: {json.dumps(password)}
masquerade:
  type: proxy
  proxy:
    url: https://bing.com/
    rewriteHost: true
bandwidth:
  up: 100 mbps
  down: 500 mbps
"""
    if socks_port is not None:
        config += f"""outbounds:
  - name: chain
    type: socks5
    socks5:
      addr: 127.0.0.1:{socks_port}
"""
    network = '    network_mode: host\n' if host_network else f'    ports:\n      - "{port}:{port}/udp"\n'
    compose = f"""  {service}:
    image: hoplyra-hysteria2:1
    container_name: {container}
    restart: unless-stopped
{network}    volumes:
      - {json.dumps(path + ":/etc/hysteria")}
"""
    uri = f"hysteria2://primary%3A{password}@{endpoint_host(host)}:{port}/?insecure=1&sni=bing.com&pinSHA256={pin_sha256}#Hoplyra-HY2"
    # The comment keeps the file valid client YAML, while exposing the import URI.
    client = f"""# {uri}
# Self-signed TLS with SHA-256 certificate pinning for server authenticity.
server: {json.dumps(f"{endpoint_host(host)}:{port}")}
auth: {json.dumps("primary:" + password)}
tls:
  sni: bing.com
  insecure: true
  pinSHA256: {pin_sha256}
socks5:
  listen: 127.0.0.1:1080
"""
    meta = {
        "port": port,
        "listenPort": port,
        "password": password,
        "username": "primary",
        "sni": "bing.com",
        "pinSHA256": pin_sha256,
        "protocol": "hysteria2",
    }
    return config, compose, client, meta, (cert_pem, key_pem)


class Hysteria2Deployer(ProtocolDeployer):
    protocol = "hysteria2"
    listen_port = 443

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        publish(config_id, "Generating Hysteria 2 configuration and self-signed certificate", stage="config")
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-hy2-{config_id[:8]}"
        port = (kwargs.get("resources") or {}).get("listenPort") or find_hysteria2_port(runner, self.listen_port)
        publish(config_id, f"Allocated port {port}/udp", stage="network")
        config, service, client, meta, (cert_pem, key_pem) = hysteria2_bundle(path, container, server_host, port)
        mkdir_remote(runner, path)
        publish(config_id, "Preparing Hysteria 2 container image", stage="image")
        ensure_image(runner, "hoplyra-hysteria2:1", "hysteria2")
        runner.upload_text(f"{path}/server.crt", cert_pem, 0o644)
        runner.upload_text(f"{path}/server.key", key_pem, 0o600)
        runner.upload_text(f"{path}/config.yaml", config, 0o600)
        runner.upload_text(f"{path}/docker-compose.yml", "services:\n" + service)
        publish(config_id, f"Starting Hysteria 2 container {container}", stage="compose")
        podman_compose_up(runner, path, self.compose_project(config_id))
        return DeployResult(client, container, path, meta)


def find_shadowquic_port(runner: RemoteRunner, base_port: int = 443) -> int:
    return find_hysteria2_port(runner, base_port)


def shadowquic_bundle(path: str, container: str, host: str, port: int, *,
                      host_network: bool = False, socks_port: int | None = None,
                      service: str = "shadowquic",
                      sni: str = "cloudflare.com") -> tuple[str, str, str, dict[str, Any]]:
    """Build ShadowQUIC server YAML, compose service, client YAML+URI, and metadata."""
    from urllib.parse import quote as url_quote

    username = "primary"
    password = secrets.token_urlsafe(16)
    bind_addr = f"[::]:{port}" if (host_network and ":" in host) else f"0.0.0.0:{port}"
    users_json = json.dumps([{"username": username, "password": password}])
    upstream_addr = f"{sni}:443"

    if socks_port is not None:
        outbounds_block = f"""outbounds:
  - tag: chain
    type: socks
    addr: {json.dumps(f"127.0.0.1:{socks_port}")}
router:
  default-outbound: chain
"""
    else:
        outbounds_block = """outbounds:
  - tag: direct-outbound
    type: direct
    dns-strategy: prefer-ipv4
router:
  default-outbound: direct-outbound
"""

    config = f"""inbounds:
  - tag: shadowquic-inbound
    type: shadowquic
    bind-addr: {json.dumps(bind_addr)}
    users: {users_json}
    jls-upstream:
      addr: {json.dumps(upstream_addr)}
    alpn: ["h3"]
    congestion-control: bbr
    zero-rtt: false
{outbounds_block}log-level: "info"
"""
    network = '    network_mode: host\n' if host_network else f'    ports:\n      - "{port}:{port}/udp"\n'
    compose = f"""  {service}:
    image: hoplyra-shadowquic:1
    container_name: {container}
    restart: unless-stopped
{network}    volumes:
      - {json.dumps(path + ":/etc/shadowquic")}
"""
    endpoint = f"{endpoint_host(host)}:{port}"
    uri = (
        f"sq://{url_quote(username, safe='')}:{url_quote(password, safe='')}@{endpoint}"
        f"?sni={url_quote(sni, safe='')}&alpn=h3&udp_mode=datagram&congestion=bbr#Hoplyra-SQ"
    )
    client = f"""# {uri}
# JLS SNI camouflage (no server TLS certificate needed).
inbounds:
  - tag: socks-inbound
    type: socks
    bind-addr: "127.0.0.1:1080"
outbounds:
  - tag: shadowquic-outbound
    type: shadowquic
    addr: {json.dumps(endpoint)}
    username: {json.dumps(username)}
    password: {json.dumps(password)}
    server-name: {json.dumps(sni)}
    alpn: ["h3"]
    initial-mtu: 1300
    congestion-control: bbr
    zero-rtt: false
    over-stream: false
"""
    meta = {
        "port": port,
        "listenPort": port,
        "password": password,
        "username": username,
        "sni": sni,
        "protocol": "shadowquic",
    }
    return config, compose, client, meta


def _wait_shadowquic_ready(
    runner: RemoteRunner,
    container: str,
    port: int,
    *,
    path: str,
    project: str,
    runtime: str | None = None,
    timeout: int = 15,
) -> None:
    rt_bin = "docker" if (runtime and "docker" in runtime) else "podman"
    container_q = shlex.quote(container)
    port_num = int(port)
    check_cmd = (
        f"for i in $(seq 1 {int(timeout * 2)}); do "
        f"  info=$({rt_bin} inspect -f '{{{{.State.Status}}}} {{{{.State.Pid}}}}' {container_q} 2>/dev/null || echo missing); "
        f"  status=${{info%% *}}; "
        f"  pid=${{info#* }}; "
        f"  if [ \"$status\" = 'exited' ] || [ \"$status\" = 'dead' ]; then "
        f"    exit 2; "
        f"  fi; "
        f"  if [ \"$status\" = 'running' ] && [ -n \"$pid\" ] && [ \"$pid\" -gt 0 ] 2>/dev/null; then "
        f"    if nsenter -t \"$pid\" -n ss -H -lun 'sport = :{port_num}' 2>/dev/null | grep -q .; then "
        f"      exit 0; "
        f"    fi; "
        f"  fi; "
        f"  sleep 0.5; "
        f"done; "
        f"exit 1"
    )
    code, _, _ = runner.run(check_cmd, timeout=timeout + 5)
    if code != 0:
        _, logs, _ = runner.run(f"{rt_bin} logs --tail 50 {container_q} 2>/dev/null", timeout=10)
        logs_detail = logs.strip() if logs else "container exited or UDP port not open"
        cleanup_error = ""
        try:
            podman_compose_down(runner, path, project, strict=True, runtime=runtime)
        except Exception as exc:
            cleanup_error = f"; cleanup failed: {exc}"
        raise RuntimeError(
            f"ShadowQUIC failed readiness check ({rt_bin}, port {port}/udp): {logs_detail}{cleanup_error}"
        )


class ShadowQuicDeployer(ProtocolDeployer):
    protocol = "shadowquic"
    listen_port = 443

    def deploy(self, runner: RemoteRunner, config_id: str, server_host: str, **kwargs: Any) -> DeployResult:
        publish(config_id, "Generating ShadowQUIC configuration (JLS SNI camouflage)", stage="config")
        path = _instance_dir(config_id, runner.target.is_local)
        container = f"cv-sq-{config_id[:8]}"
        port = (kwargs.get("resources") or {}).get("listenPort") or find_shadowquic_port(runner, self.listen_port)
        publish(config_id, f"Allocated port {port}/udp", stage="network")
        config, service, client, meta = shadowquic_bundle(path, container, server_host, port)
        mkdir_remote(runner, path)
        publish(config_id, "Preparing ShadowQUIC container image", stage="image")
        ensure_image(runner, "hoplyra-shadowquic:1", "shadowquic")
        runner.upload_text(f"{path}/config.yaml", config, 0o600)
        runner.upload_text(f"{path}/docker-compose.yml", "services:\n" + service)
        publish(config_id, f"Starting ShadowQUIC container {container}", stage="compose")
        project = self.compose_project(config_id)
        runtime = podman_compose_up(runner, path, project)
        meta["runtime"] = runtime
        publish(config_id, f"Verifying ShadowQUIC readiness on port {port}/udp", stage="compose")
        _wait_shadowquic_ready(runner, container, port, path=path, project=project, runtime=runtime)
        return DeployResult(client, container, path, meta)


DEPLOYERS: dict[str, ProtocolDeployer] = {
    "wg": WireGuardDeployer(),
    "hysteria2": Hysteria2Deployer(),
    "shadowquic": ShadowQuicDeployer(),
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
