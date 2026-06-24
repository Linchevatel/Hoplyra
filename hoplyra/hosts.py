from __future__ import annotations

import ipaddress
import re

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)
_PATH_SAFE = re.compile(r"^[a-zA-Z0-9._:-]+$")


def validate_host(host: str) -> str:
    value = host.strip()
    if not value or len(value) > 253:
        raise ValueError("Некорректный адрес сервера")
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass
    if value.endswith("."):
        value = value[:-1]
    if ".." in value or not value:
        raise ValueError("Некорректный адрес сервера")
    labels = value.split(".")
    if len(labels) < 2:
        raise ValueError("Укажите IP или полное доменное имя")
    for label in labels:
        if not _HOST_LABEL.match(label):
            raise ValueError("Некорректный адрес сервера")
    return value


def safe_path_suffix(value: str) -> str:
    if not _PATH_SAFE.fullmatch(value):
        raise ValueError(f"Unsafe path segment: {value!r}")
    return value
