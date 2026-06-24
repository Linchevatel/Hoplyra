
from __future__ import annotations

from hoplyra.container_images import _remote_has_image, ensure_image
from hoplyra.remote import RemoteRunner

SOCKS_IMAGE = "hoplyra-socks:1"
SOCKS_UPSTREAM_IMAGE = "docker.io/3proxy/3proxy:0.9.5"
SOCKS_MOUNT = "/opt/hoplyra/socks"


def ensure_socks_image(runner: RemoteRunner) -> None:
    if _remote_has_image(runner, SOCKS_UPSTREAM_IMAGE):
        return
    code, _, err = runner.run(f"podman pull {SOCKS_UPSTREAM_IMAGE}", timeout=600)
    if code == 0 and _remote_has_image(runner, SOCKS_UPSTREAM_IMAGE):
        return
    ensure_image(runner, SOCKS_IMAGE, "socks")
    if _remote_has_image(runner, SOCKS_IMAGE):
        runner.run(
            f"podman tag {SOCKS_IMAGE} {SOCKS_UPSTREAM_IMAGE} 2>/dev/null || true",
            timeout=30,
        )
        return
    raise RuntimeError(
        f"SOCKS image unavailable: pull failed ({err[:200]}), build {SOCKS_IMAGE} also failed"
    )


def build_3proxy_config(*, port: int, username: str, password: str) -> str:
    return f"""nserver 8.8.8.8
nserver 1.1.1.1
nscache 65536
timeouts 1 5 30 60 180 1800 15 60
users {username}:CL:{password}
log /dev/stdout
auth strong
socks -p{port}
"""


def compose_socks_service(container_name: str, host_path: str) -> str:
    return f"""  socks-proxy:
    image: {SOCKS_UPSTREAM_IMAGE}
    container_name: {container_name}
    network_mode: host
    command: ["/bin/3proxy", "{SOCKS_MOUNT}/3proxy.cfg"]
    volumes:
      - {host_path}:{SOCKS_MOUNT}
    restart: unless-stopped"""
