
from __future__ import annotations

import os
from pathlib import Path

                                                     
HOPYLRA_IMAGES: tuple[tuple[str, str], ...] = (
    ("hoplyra-gateway:1", "gateway"),
    ("hoplyra-tor:1", "tor"),
    ("hoplyra-xray:1", "xray"),
    ("hoplyra-wg:1", "wg"),
    ("hoplyra-openvpn:5", "openvpn"),
    ("hoplyra-socks:1", "socks"),
)


def images_root() -> Path:
    return Path(__file__).resolve().parent / "images"


def cache_dir() -> Path:
    raw = os.environ.get("HOPLYRA_IMAGE_CACHE")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / ".image-cache"


def cache_archive(image: str) -> Path:
    return cache_dir() / f"{image.replace(':', '_')}.tar.gz"


def bundle_path(bundle: str) -> Path:
    return images_root() / bundle


def all_images() -> list[tuple[str, str]]:
    return list(HOPYLRA_IMAGES)
