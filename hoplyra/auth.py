from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from hoplyra.panel_auth import verify_admin_credentials

log = logging.getLogger("hoplyra")

_PUBLIC_API = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
}


def session_secret_key() -> str:
    for env_name in ("HOPLYRA_SESSION_SECRET", "HOPLYRA_SECRET_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return "hoplyra-dev-session-insecure"


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


def require_authenticated(request: Request) -> None:
    if is_authenticated(request):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


class SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_API or not path.startswith("/api/"):
            return await call_next(request)
        try:
            require_authenticated(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return await call_next(request)


def warn_if_insecure() -> None:
    from hoplyra.panel_auth import is_default_password

    if is_default_password():
        log.warning("Panel uses default login admin/admin — change password in Settings")
    if os.environ.get("HOPLYRA_SECRET_KEY", "").strip():
        return
    log.warning("HOPLYRA_SECRET_KEY is not set — SSH passwords stored in plaintext in SQLite")
