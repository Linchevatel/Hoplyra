
from __future__ import annotations

from hoplyra.container_images import ensure_image
from hoplyra.remote import RemoteRunner

GATEWAY_IMAGE = "hoplyra-gateway:1"
GATEWAY_MOUNT = "/opt/hoplyra/gateway"


def ensure_gateway_image(runner: RemoteRunner) -> None:
    ensure_image(runner, GATEWAY_IMAGE, "gateway")


def compose_gateway_service(
    container_name: str,
    host_path: str,
    *,
    depends_on: str | None = None,
    tor_data_volume: bool = False,
) -> str:
    depends = f"\n    depends_on: [{depends_on}]" if depends_on else ""
    vol = f"""
      - {host_path}:{GATEWAY_MOUNT}"""
    if tor_data_volume:
        vol += """
      - tor-data:/var/lib/tor"""
    return f"""  chain-gateway:
    image: {GATEWAY_IMAGE}
    container_name: {container_name}
    network_mode: host
    privileged: true
    volumes:{vol}{depends}
    restart: unless-stopped"""
