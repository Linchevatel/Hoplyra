from __future__ import annotations

import threading

_CANCEL: set[str] = set()
_LOCK = threading.Lock()


class DeployCancelled(Exception):
    """Raised when a background deploy job is cancelled (e.g. user delete)."""


def request_deploy_cancel(config_id: str) -> None:
    with _LOCK:
        _CANCEL.add(config_id)


def clear_deploy_cancel(config_id: str) -> None:
    with _LOCK:
        _CANCEL.discard(config_id)


def is_deploy_cancel_requested(config_id: str) -> bool:
    with _LOCK:
        return config_id in _CANCEL


def check_deploy_cancel(config_id: str) -> None:
    if is_deploy_cancel_requested(config_id):
        raise DeployCancelled("Deploy cancelled by user")
