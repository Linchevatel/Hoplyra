
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from hoplyra import db
from hoplyra.db import connect
from hoplyra.host_setup import ensure_host_ready
from hoplyra.remote import RemoteRunner, ServerTarget, probe_server

log = logging.getLogger("hoplyra.prepare")

ProgressCb = Callable[[int, str, str], None]


@dataclass
class PrepareJob:
    server_id: str
    percent: int
    stage: str
    message: str
    status: str                          

    def to_dict(self) -> dict[str, Any]:
        return {
            "serverId": self.server_id,
            "percent": self.percent,
            "stage": self.stage,
            "message": self.message,
            "status": self.status,
        }


_JOBS: dict[str, PrepareJob] = {}
_LOCK = threading.Lock()


def get_prepare_job(server_id: str) -> PrepareJob | None:
    with _LOCK:
        return _JOBS.get(server_id)


def _set_job(server_id: str, percent: int, stage: str, message: str, status: str = "running") -> None:
    with _LOCK:
        _JOBS[server_id] = PrepareJob(server_id, percent, stage, message, status)


def _clear_job_later(server_id: str, delay_sec: int = 120) -> None:
    def _clear() -> None:
        time.sleep(delay_sec)
        with _LOCK:
            job = _JOBS.get(server_id)
            if job and job.status in ("done", "error"):
                _JOBS.pop(server_id, None)

    threading.Thread(target=_clear, daemon=True).start()


def start_prepare_job(
    server_id: str,
    target: ServerTarget,
    *,
    name: str,
    location: str | None,
    tags: list[str],
    notes: str | None,
) -> None:
    _set_job(server_id, 8, "ssh", "SSH подключён, проверка сервера…")

    def worker() -> None:
        try:
            runner = RemoteRunner(target)

            def on_progress(percent: int, message: str) -> None:
                stage = "install" if percent >= 35 else "check"
                _set_job(server_id, percent, stage, message)

            setup = ensure_host_ready(runner, on_progress=on_progress)
            _set_job(server_id, 92, "verify", "Проверка Podman и сети…")
            probe = probe_server(target)
            if probe["status"] == "connecting" and setup.get("runtime"):
                probe["status"] = "online"
                probe["podmanVersion"] = setup.get("runtime")

            status_message: str | None = None
            if setup.get("message") == "installed":
                status_message = "VPS подготовлен: Podman/Docker установлен"
            elif probe.get("message"):
                status_message = probe["message"]

            with connect() as conn:
                conn.execute(
                    """
                    UPDATE servers SET status=?, os=?, latency_ms=?, last_seen=? WHERE id=?
                    """,
                    (
                        probe["status"],
                        probe.get("os"),
                        probe.get("latencyMs"),
                        probe.get("lastSeen") or db._now(),
                        server_id,
                    ),
                )

            _set_job(server_id, 100, "done", "VPS готов к работе", "done")
            log.info("Prepare done for %s (%s)", name, target.host)
            _clear_job_later(server_id)
        except Exception as exc:
            err = str(exc)[:500]
            log.exception("Prepare failed for %s: %s", target.host, err)
            _set_job(server_id, 100, "error", err, "error")
            with connect() as conn:
                conn.execute(
                    "UPDATE servers SET status='error' WHERE id=?",
                    (server_id,),
                )
            _clear_job_later(server_id)

    threading.Thread(target=worker, daemon=True).start()
