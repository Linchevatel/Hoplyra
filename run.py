#!/usr/bin/env python3
import os
import socket

import uvicorn

from hoplyra.desktop_env import bootstrap_from_env
from hoplyra.logging_config import configure_logging
from hoplyra.main import _frontend_dist

bootstrap_from_env()
log_level = configure_logging().lower()

HOST = os.environ.get("HOPLYRA_HOST", "0.0.0.0")
PORT = int(os.environ.get("HOPLYRA_PORT", "8787"))
DESKTOP = os.environ.get("HOPLYRA_DESKTOP", "").strip().lower() in {"1", "true", "yes"}


def _display_host(bind_host: str) -> str:
    if bind_host not in ("0.0.0.0", "::"):
        return bind_host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    if not DESKTOP:
        url = f"http://{_display_host(HOST)}:{PORT}"
        ui = _frontend_dist()
        print(f"Hoplyra API: {url}/api/health")
        if ui:
            print(f"Dashboard:   {url}/")
        else:
            print("Dashboard:   not bundled (API only)")
    uvicorn.run(
        "hoplyra.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level=log_level,
        access_log=not DESKTOP,
    )
