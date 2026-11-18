from __future__ import annotations

import hashlib
import ipaddress
import logging
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("hoplyra.routing_zones")

_ZONE_FILE_URL = "https://www.ipdeny.com/ipblocks/data/countries/ru.zone"
_UPDATE_INTERVAL_SECONDS = 7 * 24 * 3600  # 1 раз в неделю
_RETRY_INTERVAL_ON_ERROR = 300.0  # При ошибке сети отдаём резервный кэш и не повторяем сетевой запрос 5 минут
_BACKGROUND_FETCH_LOCK = threading.Lock()

_CACHE_LOCK = threading.Lock()
_CACHED_ALLOWED_IPS: str | None = None
_LAST_UPDATED: float = 0.0

# Версионированный файл кэша точной 32-битной инверсии
_CACHE_FILE_NAME = "ru_zone_exact_v2.txt"


def _build_inverted_tree(ru_networks: list[ipaddress.IPv4Network]) -> list[str]:
    """Calculate exact inverse of RU subnets across all 32 bits without false inclusions."""
    root: list[Any] = [None, None, False]
    for net in ru_networks:
        node = root
        for i in range(net.prefixlen):
            bit = (int(net.network_address) >> (32 - 1 - i)) & 1
            if not node[bit]:
                node[bit] = [None, None, False]
            node = node[bit]
        node[2] = True

    result: list[str] = []

    def traverse(node: Any, prefix_int: int, depth: int) -> None:
        if not node:
            net_addr = ipaddress.IPv4Address(prefix_int << (32 - depth))
            result.append(f"{net_addr}/{depth}")
            return
        if node[2]:
            return
        traverse(node[0], (prefix_int << 1) | 0, depth + 1)
        traverse(node[1], (prefix_int << 1) | 1, depth + 1)

    traverse(root, 0, 0)
    return result


# Минимальное число префиксов для признания кэша корректным (защита от усечённых файлов)
_MIN_INVERTED_ENTRIES = 5000


def _parse_and_validate_disk_cache(raw_text: str) -> str | None:
    """Validate checksum and IPv4 CIDR syntax for every entry read from disk."""
    import hmac

    if not raw_text:
        return None
    lines = raw_text.splitlines()
    if not lines:
        return None

    # 1. Проверка контрольной суммы
    header = lines[0].strip()
    if not header.startswith("# checksum: sha256:"):
        return None
    expected_sha = header.split("sha256:", 1)[1].strip()
    body = "\n".join(lines[1:]).strip()
    if not body:
        return None
    actual_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_sha, expected_sha):
        log.warning("ru_zone_cache checksum mismatch: expected %s, got %s", expected_sha, actual_sha)
        return None

    # 2. Валидация всех записей как IPv4 CIDR
    entries = [e.strip() for e in body.split(",") if e.strip()]
    if len(entries) < _MIN_INVERTED_ENTRIES:
        log.warning("ru_zone_cache has too few entries: %d", len(entries))
        return None

    try:
        for entry in entries:
            net = ipaddress.IPv4Network(entry, strict=False)
            if net.version != 4:
                return None
    except Exception as exc:
        log.warning("ru_zone_cache contains invalid CIDR entry: %s", exc)
        return None

    return body


def fetch_and_calculate_bypass_ru(cache_dir: Path | None = None) -> str:
    """Fetch RU zone file from ipdeny, compute inverted non-RU AllowedIPs, overwrite single cache file."""
    global _CACHED_ALLOWED_IPS, _LAST_UPDATED

    zone_file = (cache_dir / _CACHE_FILE_NAME) if cache_dir else None

    # 1. Проверяем свежий валидный кеш в памяти (в памяти хранится уже проверенная строка)
    with _CACHE_LOCK:
        if _CACHED_ALLOWED_IPS and time.time() - _LAST_UPDATED < _UPDATE_INTERVAL_SECONDS:
            return _CACHED_ALLOWED_IPS

    # 2. Проверяем файл на диске с проверкой контрольной суммы и валидацией всех CIDR
    if zone_file and zone_file.is_file():
        try:
            raw_text = zone_file.read_text(encoding="utf-8").strip()
            mtime = zone_file.stat().st_mtime
            validated_body = _parse_and_validate_disk_cache(raw_text)
            if validated_body:
                with _CACHE_LOCK:
                    _CACHED_ALLOWED_IPS = validated_body
                    _LAST_UPDATED = mtime
                if time.time() - mtime < _UPDATE_INTERVAL_SECONDS:
                    return validated_body
        except Exception as exc:
            log.warning("Failed to read/validate local ru_zone_cache: %s", exc)

    log.info("Downloading fresh ru.zone from %s", _ZONE_FILE_URL)
    req = urllib.request.Request(_ZONE_FILE_URL, headers={"User-Agent": "Hoplyra/1.6.1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            lines = [
                line.strip()
                for line in resp.read().decode("utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]
        if len(lines) < 1000:
            raise ValueError(f"Downloaded ru.zone contains too few entries ({len(lines)}), potential truncation or empty response")
        parsed_nets = [ipaddress.ip_network(l) for l in lines]
        collapsed = list(ipaddress.collapse_addresses(parsed_nets))
        inverted = _build_inverted_tree(collapsed)
        if len(inverted) < _MIN_INVERTED_ENTRIES:
            raise ValueError(f"Inverted zone contains too few entries ({len(inverted)})")
        result_str = ", ".join(inverted)
    except Exception as exc:
        log.warning("Failed to fetch/calculate fresh ru.zone: %s", exc)
        with _CACHE_LOCK:
            if _CACHED_ALLOWED_IPS:
                log.info("Using existing in-memory cache for bypass-ru (grace period 5m)")
                _LAST_UPDATED = time.time() - _UPDATE_INTERVAL_SECONDS + _RETRY_INTERVAL_ON_ERROR
                return _CACHED_ALLOWED_IPS
        if zone_file and zone_file.is_file():
            try:
                raw_text = zone_file.read_text(encoding="utf-8").strip()
                validated_body = _parse_and_validate_disk_cache(raw_text)
                if validated_body:
                    log.info("Using existing on-disk cache for bypass-ru (grace period 5m)")
                    with _CACHE_LOCK:
                        _CACHED_ALLOWED_IPS = validated_body
                        _LAST_UPDATED = time.time() - _UPDATE_INTERVAL_SECONDS + _RETRY_INTERVAL_ON_ERROR
                    return validated_body
            except Exception:
                pass
        raise RuntimeError("База подсетей РФ временно недоступна. Попробуйте позже.") from exc

    # Атомарно записываем файл вместе с SHA256-контрольной суммой через уникальный временный файл
    if zone_file:
        tmp_path: Path | None = None
        try:
            zone_file.parent.mkdir(parents=True, exist_ok=True)
            fd, raw_tmp = tempfile.mkstemp(
                prefix=f"{_CACHE_FILE_NAME}.tmp-",
                dir=str(zone_file.parent),
            )
            tmp_path = Path(raw_tmp)
            checksum = hashlib.sha256(result_str.encode("utf-8")).hexdigest()
            payload_with_meta = f"# checksum: sha256:{checksum}\n{result_str}\n"
            with open(fd, "w", encoding="utf-8") as f:
                f.write(payload_with_meta)
            tmp_path.replace(zone_file)
        except Exception as exc:
            log.warning("Could not persist ru_zone_cache: %s", exc)
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    with _CACHE_LOCK:
        _CACHED_ALLOWED_IPS = result_str
        _LAST_UPDATED = time.time()

    log.info("Successfully generated non-RU AllowedIPs (%d subnets)", len(inverted))
    return result_str


def get_bypass_ru_allowed_ips(cache_dir: Path | None = None) -> str:
    """Return currently cached non-RU AllowedIPs string or calculate."""
    global _CACHED_ALLOWED_IPS, _LAST_UPDATED

    zone_file = (cache_dir / _CACHE_FILE_NAME) if cache_dir else None

    # 1. Быстрая отдача из проверенного кэша в памяти
    with _CACHE_LOCK:
        if _CACHED_ALLOWED_IPS:
            is_stale = (time.time() - _LAST_UPDATED >= _UPDATE_INTERVAL_SECONDS)
            if not is_stale:
                return _CACHED_ALLOWED_IPS
            # Если кэш просрочен, запускаем фоновое обновление без блокировки вызывающего
            _trigger_async_refresh(cache_dir)
            return _CACHED_ALLOWED_IPS

    # 2. Быстрая отдача из файла на диске
    if zone_file and zone_file.is_file():
        try:
            raw_text = zone_file.read_text(encoding="utf-8").strip()
            mtime = zone_file.stat().st_mtime
            validated_body = _parse_and_validate_disk_cache(raw_text)
            if validated_body:
                with _CACHE_LOCK:
                    _CACHED_ALLOWED_IPS = validated_body
                    _LAST_UPDATED = mtime
                if time.time() - mtime >= _UPDATE_INTERVAL_SECONDS:
                    _trigger_async_refresh(cache_dir)
                return validated_body
        except Exception as exc:
            log.warning("Failed fast read from disk cache: %s", exc)

    # 3. Синхронный расчёт только при первом холодном старте (когда кэша нет совсем)
    return fetch_and_calculate_bypass_ru(cache_dir)


def _trigger_async_refresh(cache_dir: Path | None) -> None:
    def _worker() -> None:
        if not _BACKGROUND_FETCH_LOCK.acquire(blocking=False):
            return
        try:
            fetch_and_calculate_bypass_ru(cache_dir)
        except Exception as exc:
            log.debug("Async zone refresh error: %s", exc)
        finally:
            _BACKGROUND_FETCH_LOCK.release()

    threading.Thread(target=_worker, name="hoplyra-async-zone-refresh", daemon=True).start()


def start_weekly_zone_updater(cache_dir: Path) -> None:
    """Daemon thread checking and downloading ru.zone once a week."""
    def _loop() -> None:
        time.sleep(10)  # startup delay
        while True:
            try:
                fetch_and_calculate_bypass_ru(cache_dir)
            except Exception as exc:
                log.debug("Weekly zone updater error: %s", exc)
            time.sleep(24 * 3600)  # check daily if 7 days elapsed

    threading.Thread(target=_loop, name="hoplyra-zone-updater", daemon=True).start()
