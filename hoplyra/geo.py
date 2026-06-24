from __future__ import annotations

import json
import logging
import urllib.request
from urllib.error import URLError

from hoplyra.remote import is_local_host

log = logging.getLogger("hoplyra.geo")

_GEO_FAILED: set[str] = set()


def lookup_location(host: str) -> str | None:
    host = host.strip()
    if not host or is_local_host(host):
        return None

    try:
        req = urllib.request.Request(
            f"https://ipwho.is/{host}",
            headers={"User-Agent": "Hoplyra/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        log.debug("geo lookup failed for %s: %s", host, exc)
        return None

    if not data.get("success") or not data.get("country_code"):
        return None

    country_code = str(data["country_code"]).upper()
    city = str(data.get("city") or "").strip()
    return f"{country_code}, {city}" if city else country_code


def ensure_location(server: dict[str, object]) -> dict[str, object]:
    if server.get("location"):
        return server

    host = str(server.get("host") or "").strip()
    if not host or host in _GEO_FAILED:
        return server

    location = lookup_location(host)
    if not location:
        _GEO_FAILED.add(host)
        return server

    server_id = server.get("id")
    if server_id:
        from hoplyra.db import connect

        with connect() as conn:
            conn.execute(
                "UPDATE servers SET location=? WHERE id=? AND (location IS NULL OR location='')",
                (location, server_id),
            )

    server["location"] = location
    return server
