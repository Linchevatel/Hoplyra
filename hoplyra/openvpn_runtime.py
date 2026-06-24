
from __future__ import annotations

from pathlib import Path

from hoplyra.container_images import ensure_image
from hoplyra.remote import RemoteRunner, wait_for_remote_file

OPENVPN_IMAGE = "hoplyra-openvpn:5"
OPENVPN_MOUNT = "/opt/hoplyra/openvpn"
IMAGE_CTX = "/opt/hoplyra/images/openvpn"


def _assets() -> dict[str, Path]:
    base = Path(__file__).resolve().parent / "images" / "openvpn"
    return {
        "Dockerfile": base / "Dockerfile",
        "start.sh": base / "start.sh",
        "setup-pki.sh": base / "setup-pki.sh",
    }


def server_conf_content(transport: str, listen_port: int = 1194) -> str:
    exit_notify = "explicit-exit-notify 1\n" if transport == "udp" else ""
    return f"""port {listen_port}
proto {transport}
dev tun
ca {OPENVPN_MOUNT}/ca.crt
cert {OPENVPN_MOUNT}/server.crt
key {OPENVPN_MOUNT}/server.key
dh none
ecdh-curve prime256v1
tls-auth {OPENVPN_MOUNT}/ta.key 0
tls-version-min 1.2
server 10.8.0.0 255.255.255.0
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 1.1.1.1"
keepalive 10 120
{exit_notify}cipher AES-256-GCM
user nobody
group nobody
persist-key
persist-tun
verb 0
"""


def build_client_ovpn(
    runner: RemoteRunner,
    path: str,
    server_host: str,
    transport: str = "udp",
    listen_port: int = 1194,
) -> str:
    ca = f"{path}/ca.crt"
    if not wait_for_remote_file(runner, ca, attempts=40, delay_sec=5):
        return (
            f"client\ndev tun\nproto {transport}\nremote {server_host} {listen_port}\n"
            f"resolv-retry infinite\nnobind\npersist-key\npersist-tun\n"
            f"remote-cert-tls server\ncipher AES-256-GCM\nverb 3\n"
        )

    script = (
        f"echo 'client'; echo 'dev tun'; echo 'proto {transport}'; "
        f"echo 'remote {server_host} {listen_port}'; "
        f"echo 'resolv-retry infinite'; echo 'nobind'; echo 'persist-key'; echo 'persist-tun'; "
        f"echo 'remote-cert-tls server'; echo 'cipher AES-256-GCM'; echo 'verb 3'; "
        f"echo 'tls-version-min 1.2'; echo 'key-direction 1'; "
        f"echo '<ca>'; cat {path}/ca.crt; echo '</ca>'; "
        f"echo '<cert>'; cat {path}/client.crt; echo '</cert>'; "
        f"echo '<key>'; cat {path}/client.key; echo '</key>'; "
        f"echo '<tls-auth>'; cat {path}/ta.key; echo '</tls-auth>';"
    )
    _, client_full, _ = runner.run(script)
    return client_full


def compose_service(container_name: str, host_path: str, *, service_name: str = "openvpn", skip_wan_nat: bool = False) -> str:
    env = "\n    environment:\n      - OPENVPN_SKIP_WAN_NAT=1" if skip_wan_nat else ""
    return f"""  {service_name}:
    image: {OPENVPN_IMAGE}
    container_name: {container_name}
    network_mode: host
    cap_add: [NET_ADMIN]
    devices: [/dev/net/tun]
    volumes:
      - {host_path}:{OPENVPN_MOUNT}
    restart: unless-stopped{env}"""


def compose_ovpn_client_service(container_name: str, host_path: str, *, service_name: str = "openvpn-chain-client") -> str:
    return compose_service(container_name, host_path, service_name=service_name, skip_wan_nat=True)


def build_chain_client_ovpn_conf(
    exit_host: str,
    transport: str,
    material: dict[str, str],
    *,
    tor_socks: tuple[str, int] | None = None,
    listen_port: int = 1194,
) -> str:
    lines = [
        "client",
        "dev tun",
        f"proto {transport}",
        f"remote {exit_host} {listen_port}",
        "nobind",
        "persist-key",
        "persist-tun",
        "remote-cert-tls server",
        "cipher AES-256-GCM",
        "tls-version-min 1.2",
        "key-direction 1",
        "verb 3",
    ]
    if tor_socks:
        lines.insert(4, f"socks-proxy {tor_socks[0]} {tor_socks[1]}")
    body = "\n".join(lines) + "\n"
    for tag, key in (("ca", "ca.crt"), ("cert", "client.crt"), ("key", "client.key"), ("tls-auth", "ta.key")):
        content = material.get(key, "").strip()
        if content:
            body += f"<{tag}>\n{content}\n</{tag}>\n"
    return body


def wait_for_tun_interface(runner: RemoteRunner, *, timeout_sec: int = 180) -> str:
    last_diag = ""
    for _ in range(max(1, timeout_sec // 2)):
        _, out, _ = runner.run(
            "ip -o link show type tun 2>/dev/null | awk -F': ' '{print $2}' | head -1"
        )
        name = out.strip().split("@")[0]
        if name:
            _, ipout, _ = runner.run(
                f"ip -4 addr show dev {name} 2>/dev/null | grep -F 'inet ' || true"
            )
            if ipout.strip():
                return name
        _, last_diag, _ = runner.run(
            "pgrep -a openvpn 2>/dev/null | head -3; "
            "ss -tln 2>/dev/null | grep ':9050 ' || true"
        )
        runner.run("sleep 2")
    raise RuntimeError(
        "OpenVPN chain tunnel did not come up in time"
        + (f" (last: {last_diag.strip()[:300]})" if last_diag.strip() else "")
    )


def _runtime_cmd(runner: RemoteRunner, container: str, script: str) -> str:
    return f"podman exec {container} {script}"


def wait_for_container(runner: RemoteRunner, name: str, timeout_sec: int = 120) -> None:
    for _ in range(max(1, timeout_sec // 2)):
        code, out, _ = runner.run(
            f"podman ps --format '{{{{.Names}}}}' 2>/dev/null | grep -Fx '{name}' || true"
        )
        if name in out.splitlines():
            code, _, _ = runner.run(f"podman exec {name} true 2>/dev/null")
            if code == 0:
                return
        runner.run("sleep 2")
    raise RuntimeError(f"container {name} not ready on {runner.target.host}")


def ensure_openvpn_image(runner: RemoteRunner, *, chain_client: bool = False) -> None:
    ensure_image(
        runner,
        OPENVPN_IMAGE,
        "openvpn",
        verify_entrypoint_contains="client-chain.conf" if chain_client else None,
    )


def setup_pki_in_container(runner: RemoteRunner, container: str) -> None:
    code, _, err = runner.run(_runtime_cmd(runner, container, "/opt/hoplyra/setup-pki.sh"))
    if code != 0:
        raise RuntimeError(f"OpenVPN PKI setup failed in {container}: {err[:500]}")


def reload_openvpn_daemon(runner: RemoteRunner, container: str) -> None:
    cmd = (
        "sh -c 'killall openvpn 2>/dev/null || true; "
        "openvpn --config /opt/hoplyra/openvpn/server.conf --daemon'"
    )
    code, _, err = runner.run(_runtime_cmd(runner, container, cmd))
    if code != 0:
        raise RuntimeError(f"OpenVPN start failed in {container}: {err[:500]}")


def ensure_openvpn_port_free(runner: RemoteRunner, port: int = 1194, *, chain_tag: str | None = None) -> None:
    if chain_tag:
        name_filter = f"grep -E 'cv-ovpn-(exit|entry|chain)-{chain_tag}'"
    else:
        name_filter = "grep -E 'cv-ovpn'"
    runner.run(
        f"for c in $(podman ps -a --format '{{{{.Names}}}}' 2>/dev/null | "
        f"{name_filter}); do podman rm -f \"$c\" 2>/dev/null || true; done; "
        "killall openvpn 2>/dev/null || true"
    )
    for _ in range(12):
        code, out, _ = runner.run(
            f"ss -lun 2>/dev/null | grep -F ':{port} ' || true"
        )
        if f":{port}" not in out:
            return
        runner.run("killall openvpn 2>/dev/null || true")
        runner.run(
            "podman ps -a --format '{{.Names}}' 2>/dev/null | "
            "grep -E 'cv-ovpn' | xargs -r podman rm -f 2>/dev/null || true"
        )
        runner.run("sleep 2")


def provision_openvpn_instance(
    runner: RemoteRunner,
    host_path: str,
    container: str,
    transport: str,
    *,
    listen_port: int = 1194,
) -> None:
    wait_for_container(runner, container)
    setup_pki_in_container(runner, container)
    reload_openvpn_daemon(runner, container)
