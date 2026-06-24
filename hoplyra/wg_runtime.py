
from __future__ import annotations

from hoplyra.container_images import ensure_image
from hoplyra.remote import RemoteRunner

WG_IMAGE = "hoplyra-wg:1"
WG_CONF_IN_CONTAINER = "/etc/wireguard/wg0.conf"

HOST_WG_TEARDOWN = (
    f"wg-quick down {WG_CONF_IN_CONTAINER} 2>/dev/null || true; "
    "ip link del wg0 2>/dev/null || true"
)

                                                                                   
HOST_GATEWAY_IPTABLES_CLEANUP = """
while iptables -t nat -C PREROUTING -i wg0 -p tcp -s 10.66.66.0/24 -j REDIRECT --to-ports 12345 2>/dev/null; do
  iptables -t nat -D PREROUTING -i wg0 -p tcp -s 10.66.66.0/24 -j REDIRECT --to-ports 12345
done
while iptables -t nat -C PREROUTING -i awg0 -p tcp -s 10.9.1.0/24 -j REDIRECT --to-ports 12345 2>/dev/null; do
  iptables -t nat -D PREROUTING -i awg0 -p tcp -s 10.9.1.0/24 -j REDIRECT --to-ports 12345
done
while iptables -t nat -C PREROUTING -i tun0 -p tcp -s 10.8.0.0/24 -j REDIRECT --to-ports 12345 2>/dev/null; do
  iptables -t nat -D PREROUTING -i tun0 -p tcp -s 10.8.0.0/24 -j REDIRECT --to-ports 12345
done
while iptables -t nat -C PREROUTING -i tun0 -p tcp -j REDIRECT --to-ports 12345 2>/dev/null; do
  iptables -t nat -D PREROUTING -i tun0 -p tcp -j REDIRECT --to-ports 12345
done
""".strip()

def ensure_wg_image(runner: RemoteRunner) -> None:
    ensure_image(runner, WG_IMAGE, "wg")


def compose_wg_service(
    container_name: str,
    host_conf_path: str,
    *,
    service_name: str = "wg",
    cap_sys_module: bool = False,
) -> str:
    caps = "[NET_ADMIN, SYS_MODULE]" if cap_sys_module else "[NET_ADMIN]"
    return f"""  {service_name}:
    image: {WG_IMAGE}
    container_name: {container_name}
    network_mode: host
    cap_add: {caps}
    volumes:
      - {host_conf_path}:{WG_CONF_IN_CONTAINER}:ro
    restart: unless-stopped"""
