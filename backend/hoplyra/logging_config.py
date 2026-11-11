from __future__ import annotations

import logging
import os


def configure_logging() -> str:
    default = "WARNING" if os.environ.get("HOPLYRA_DESKTOP", "").strip().lower() in {"1", "true", "yes"} else "INFO"
    level_name = os.environ.get("HOPLYRA_LOG_LEVEL", default).strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    for name in (
        "hoplyra",
        "hoplyra.prepare",
        "hoplyra.geo",
        "hoplyra.metrics_cache",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
    ):
        logging.getLogger(name).setLevel(level)

    logging.getLogger("paramiko").setLevel(logging.CRITICAL)
    logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)

    return level_name
