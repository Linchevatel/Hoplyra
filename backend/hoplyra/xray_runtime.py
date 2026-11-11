
from __future__ import annotations

from hoplyra.container_images import ensure_image
from hoplyra.remote import RemoteRunner

XRAY_IMAGE = "hoplyra-xray:1"
XRAY_MOUNT = "/opt/hoplyra/xray"


def ensure_xray_image(runner: RemoteRunner) -> None:
    ensure_image(runner, XRAY_IMAGE, "xray")


def tls_cert_paths() -> tuple[str, str]:
    return f"{XRAY_MOUNT}/cert.pem", f"{XRAY_MOUNT}/key.pem"


def compose_xray_service(
    container_name: str,
    host_path: str,
    *,
    service_name: str = "xray",
    config_file: str = "config.json",
    tls_cn: str | None = None,
    depends_on: str | None = None,
) -> str:
    env = f'      - XRAY_CONFIG={XRAY_MOUNT}/{config_file}\n'
    if tls_cn:
        env += f'      - TLS_CN={tls_cn}\n'
    depends = f"\n    depends_on: [{depends_on}]" if depends_on else ""
    return f"""  {service_name}:
    image: {XRAY_IMAGE}
    container_name: {container_name}
    network_mode: host
    environment:
{env.rstrip()}
    volumes:
      - {host_path}:{XRAY_MOUNT}
    restart: unless-stopped{depends}"""
