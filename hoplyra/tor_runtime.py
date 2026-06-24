
from __future__ import annotations

from hoplyra.container_images import ensure_image
from hoplyra.remote import RemoteRunner

TOR_IMAGE = "hoplyra-tor:1"
TOR_MOUNT = "/opt/hoplyra/tor"


def ensure_tor_image(runner: RemoteRunner) -> None:
    ensure_image(runner, TOR_IMAGE, "tor")


def compose_tor_service(
    container_name: str,
    host_path: str,
    *,
    service_name: str = "tor",
    tor_data_volume: bool = False,
    privileged: bool = False,
) -> str:
    priv = "\n    privileged: true" if privileged else ""
    vol_lines = f"      - {host_path}:{TOR_MOUNT}"
    if tor_data_volume:
        vol_lines += "\n      - tor-data:/var/lib/tor"
    return f"""  {service_name}:
    image: {TOR_IMAGE}
    container_name: {container_name}
    network_mode: host{priv}
    volumes:
{vol_lines}
    restart: unless-stopped"""
