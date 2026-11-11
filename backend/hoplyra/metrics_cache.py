from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from hoplyra import db
from hoplyra.server_metrics import CONTROL_SERVER_ID, collect_local_metrics, collect_metrics_batch

log = logging.getLogger("hoplyra.metrics_cache")

RowLoader = Callable[[], list[Any]]


class MetricsCache:
    def __init__(self, interval: float = 1.0) -> None:
        self._interval = interval
        self._lock = threading.Lock()
        self._metrics: list[dict[str, Any]] = []
        self._ts: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loader: RowLoader | None = None

    def start(self, loader: RowLoader) -> None:
        self._loader = loader
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hoplyra-metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"metrics": list(self._metrics), "ts": self._ts or db._now()}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                log.exception("metrics cache refresh failed")
            self._stop.wait(self._interval)

    def refresh(self) -> list[dict[str, Any]]:
        loader = self._loader
        rows = loader() if loader else []
        control = collect_local_metrics()
        remote = collect_metrics_batch(rows) if rows else []
        payload = [control, *remote]
        ts = db._now()
        with self._lock:
            self._metrics = payload
            self._ts = ts
        return payload


metrics_cache = MetricsCache(interval=5.0)
