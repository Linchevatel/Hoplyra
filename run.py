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


SSL_CERTFILE = os.environ.get("HOPLYRA_SSL_CERTFILE") or os.environ.get("SSL_CERTFILE")
SSL_KEYFILE = os.environ.get("HOPLYRA_SSL_KEYFILE") or os.environ.get("SSL_KEYFILE")
SSL_KEY_PASSWORD = os.environ.get("HOPLYRA_SSL_KEY_PASSWORD") or os.environ.get("SSL_KEY_PASSWORD")
IS_SSL = bool(SSL_CERTFILE and SSL_KEYFILE)
SCHEME = "https" if IS_SSL else "http"


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
        url = f"{SCHEME}://{_display_host(HOST)}:{PORT}"
        ui = _frontend_dist()
        print(f"Hoplyra API: {url}/api/health")
        if ui:
            print(f"Dashboard:   {url}/")
        else:
            print("Dashboard:   not bundled (API only)")

    uvicorn_kwargs: dict[str, object] = {
        "host": HOST,
        "port": PORT,
        "reload": False,
        "log_level": log_level,
        "access_log": not DESKTOP,
    }
    if IS_SSL:
        uvicorn_kwargs["ssl_certfile"] = SSL_CERTFILE
        uvicorn_kwargs["ssl_keyfile"] = SSL_KEYFILE
        if SSL_KEY_PASSWORD:
            uvicorn_kwargs["ssl_keyfile_password"] = SSL_KEY_PASSWORD

    uvicorn.run("hoplyra.main:app", **uvicorn_kwargs)

