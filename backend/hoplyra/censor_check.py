"""Server censor & reachability verification module (Precise RKN Registry + RU Outbound Probe).

1. Offline Exact Registry Check:
   - Level A (Exact Match): IP is explicitly in RKN exact list (ip.lst) or whole subnet list (subnet.lst).
     -> rknBlocked = True, rknExact = True
   - Level B (Neighborhood /24 Warning): Another IP in the same /24 is in RKN list, but this IP itself is NOT blocked.
     -> rknBlocked = False, rknExact = False, rknSubnetNeighbor = "x.x.x.0/24"
2. Outbound Probe: Target VPS verifies connectivity to reference Russian infrastructure:
   - 77.88.8.8:53 (Yandex DNS)
   - 77.88.55.242:443 (ya.ru)
   - 87.240.129.133:443 (vk.com)
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoplyra.remote import RemoteRunner

log = logging.getLogger("hoplyra.censor_check")

_CACHE_FILE_EXACT = "rkn_exact_blocked.txt"
_CACHE_FILE_NEIGHBOR = "rkn_neighbor_subnets.txt"
_UPDATE_INTERVAL_SECONDS = 6 * 3600.0  # 6 hours
_RETRY_INTERVAL_ON_ERROR = 300.0  # 5 min backoff on download error

_EXACT_IP_SOURCES = [
    "https://antifilter.download/list/ip.lst",
    "https://antifilter.network/download/ip.lst",
]
_EXACT_SUBNET_SOURCES = [
    "https://antifilter.download/list/subnet.lst",
    "https://antifilter.network/download/subnet.lst",
]
_NEIGHBOR_SOURCES = [
    "https://antifilter.download/list/ipsum.lst",
    "https://antifilter.network/download/ipsum.lst",
]

_CACHE_LOCK = threading.Lock()
_BACKGROUND_FETCH_LOCK = threading.Lock()

# Radix trees:
_EXACT_TREE: dict[str, Any] = {}
_NEIGHBOR_TREE: dict[str, Any] = {}
_LAST_UPDATED: float = 0.0
_LOADED_CACHE_DIR: Path | None = None

# Memory cache for server probe results: server_id -> CensorResult dict
_SERVER_RESULTS_CACHE: dict[str, dict[str, Any]] = {}
_SERVER_RESULTS_LOCK = threading.Lock()


@dataclass
class CensorCheckResult:
    rkn_blocked: bool
    rkn_exact: bool
    rkn_subnet: str | None
    ru_reachability: str  # "ok" | "degraded" | "blocked" | "unknown"
    ru_latency_ms: int | None
    details: list[dict[str, Any]]
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rknBlocked": self.rkn_blocked,
            "rknExact": self.rkn_exact,
            "rknSubnet": self.rkn_subnet,
            "ruReachability": self.ru_reachability,
            "ruLatencyMs": self.ru_latency_ms,
            "details": self.details,
            "checkedAt": self.checked_at,
        }


def _ip_to_uint32(ip_str: str) -> int:
    return int(ipaddress.IPv4Address(ip_str))


def _build_radix_tree(lines: list[str]) -> dict[str, Any]:
    """Build a 32-bit binary trie from IPv4 CIDR subnets."""
    root: dict[str, Any] = {}
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            if "/" in raw:
                net = ipaddress.IPv4Network(raw, strict=False)
            else:
                net = ipaddress.IPv4Network(f"{raw}/32", strict=False)
        except ValueError:
            continue

        ip_int = int(net.network_address)
        prefix = net.prefixlen

        curr = root
        for i in range(31, 31 - prefix, -1):
            bit = (ip_int >> i) & 1
            if bit not in curr:
                curr[bit] = {}
            curr = curr[bit]
        curr["leaf"] = str(net)
    return root


def _match_radix_tree(tree: dict[str, Any], ip_str: str) -> str | None:
    """Check if IPv4 address falls into any subnet in the radix tree."""
    try:
        ip_int = _ip_to_uint32(ip_str)
    except ValueError:
        return None

    curr = tree
    for i in range(31, -1, -1):
        if "leaf" in curr:
            return curr["leaf"]
        bit = (ip_int >> i) & 1
        if bit not in curr:
            return None
        curr = curr[bit]
    return curr.get("leaf")


def _download_first_ok(urls: list[str]) -> str | None:
    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Hoplyra-CensorCheck/1.7 (+https://github.com/Linchevatel/Hoplyra)"},
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if resp.status == 200:
                    data = resp.read().decode("utf-8", errors="ignore")
                    if len(data) > 100:
                        return data
        except Exception as exc:
            log.warning("Censor check: download failed from %s: %s", url, exc)
    return None


def _load_cached_subnets(cache_file: Path | None) -> list[str]:
    if not cache_file or not cache_file.is_file():
        return []

    try:
        raw_text = cache_file.read_text(encoding="utf-8").strip()
        lines = raw_text.splitlines()
        if not lines:
            return []

        if lines[0].startswith("# SHA256:"):
            expected_hash = lines[0].split(":", 1)[1].strip()
            body = "\n".join(lines[1:])
            actual_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if actual_hash != expected_hash:
                log.warning("Cache SHA-256 mismatch for %s, discarding", cache_file.name)
                return []
            return lines[1:]
        return lines
    except Exception as exc:
        log.warning("Failed to load cache %s: %s", cache_file.name, exc)
        return []


def _save_cache_file(target_file: Path, content: str) -> None:
    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        body = content.strip()
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        payload = f"# SHA256:{content_hash}\n{body}\n"

        tmp_file = target_file.parent / f"{target_file.name}.tmp.{int(time.time())}"
        tmp_file.write_text(payload, encoding="utf-8")
        tmp_file.replace(target_file)
    except Exception as exc:
        log.warning("Failed saving cache %s: %s", target_file.name, exc)


def ensure_rkn_database(cache_dir: Path | None = None) -> None:
    """Ensure exact and neighbor RKN databases are loaded in memory."""
    global _EXACT_TREE, _NEIGHBOR_TREE, _LAST_UPDATED, _LOADED_CACHE_DIR

    exact_file = (cache_dir / _CACHE_FILE_EXACT) if cache_dir else None
    neighbor_file = (cache_dir / _CACHE_FILE_NEIGHBOR) if cache_dir else None

    with _CACHE_LOCK:
        if _EXACT_TREE and _LOADED_CACHE_DIR == cache_dir:
            if time.time() - _LAST_UPDATED < _UPDATE_INTERVAL_SECONDS:
                return
            _trigger_async_refresh(cache_dir)
            return

    exact_lines = _load_cached_subnets(exact_file)
    neighbor_lines = _load_cached_subnets(neighbor_file)

    if exact_lines:
        exact_tree = _build_radix_tree(exact_lines)
        neighbor_tree = _build_radix_tree(neighbor_lines) if neighbor_lines else {}
        with _CACHE_LOCK:
            _EXACT_TREE = exact_tree
            _NEIGHBOR_TREE = neighbor_tree
            _LOADED_CACHE_DIR = cache_dir
            _LAST_UPDATED = exact_file.stat().st_mtime if exact_file and exact_file.is_file() else time.time()

        if time.time() - _LAST_UPDATED >= _UPDATE_INTERVAL_SECONDS:
            _trigger_async_refresh(cache_dir)
        return

    _fetch_and_build(cache_dir)


def _parse_valid_lines(raw: str | None, min_valid: int = 10) -> list[str] | None:
    """Validate that downloaded payload contains a minimum threshold of valid IPv4/CIDR lines."""
    if not raw:
        return None
    valid: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if "/" in line:
                ipaddress.IPv4Network(line, strict=False)
            else:
                ipaddress.IPv4Address(line)
            valid.append(line)
        except ValueError:
            continue
    if len(valid) < min_valid:
        return None
    return valid


def _fetch_and_build(cache_dir: Path | None) -> None:
    global _EXACT_TREE, _NEIGHBOR_TREE, _LAST_UPDATED, _LOADED_CACHE_DIR

    exact_ip_raw = _download_first_ok(_EXACT_IP_SOURCES)
    exact_subnet_raw = _download_first_ok(_EXACT_SUBNET_SOURCES)
    neighbor_raw = _download_first_ok(_NEIGHBOR_SOURCES)

    # Замечание [3]: Проверяем каждый набор отдельно до объединения (защита от повреждённого HTML/ошибок)
    ip_lines = _parse_valid_lines(exact_ip_raw, min_valid=500)
    subnet_lines = _parse_valid_lines(exact_subnet_raw, min_valid=10)
    neighbor_lines = _parse_valid_lines(neighbor_raw, min_valid=500)

    if not ip_lines or not subnet_lines:
        log.warning("Censor check: incomplete or invalid RKN sources, keeping existing cache")
        with _CACHE_LOCK:
            _LAST_UPDATED = time.time() - _UPDATE_INTERVAL_SECONDS + _RETRY_INTERVAL_ON_ERROR
        return

    combined_exact_lines = ip_lines + subnet_lines
    exact_tree = _build_radix_tree(combined_exact_lines)

    # Замечание [2]: При сбое загрузки ipsum.lst сохраняем последнее валидное дерево соседей
    if neighbor_lines:
        neighbor_tree = _build_radix_tree(neighbor_lines)
    else:
        log.warning("Censor check: neighbor list download failed/invalid, retaining previous neighbor cache")
        with _CACHE_LOCK:
            neighbor_tree = _NEIGHBOR_TREE if _NEIGHBOR_TREE else {}

    with _CACHE_LOCK:
        _EXACT_TREE = exact_tree
        _NEIGHBOR_TREE = neighbor_tree
        _LOADED_CACHE_DIR = cache_dir
        _LAST_UPDATED = time.time()

    if cache_dir:
        _save_cache_file(cache_dir / _CACHE_FILE_EXACT, "\n".join(combined_exact_lines))
        if neighbor_lines:
            _save_cache_file(cache_dir / _CACHE_FILE_NEIGHBOR, "\n".join(neighbor_lines))


def _trigger_async_refresh(cache_dir: Path | None) -> None:
    def _worker():
        if not _BACKGROUND_FETCH_LOCK.acquire(blocking=False):
            return
        try:
            _fetch_and_build(cache_dir)
        finally:
            _BACKGROUND_FETCH_LOCK.release()

    t = threading.Thread(target=_worker, name="hoplyra-rkn-refresh", daemon=True)
    t.start()


def is_ip_in_rkn_registry(host: str, cache_dir: Path | None = None) -> tuple[bool | None, bool, str | None, list[str]]:
    """Check if host (IP or domain) matches RKN lists.
    
    Returns (is_blocked_exact, is_neighbor_warning, matched_subnet, resolved_ips).
    - If registry cache not ready / empty: is_blocked_exact = None
    - If exact block: (True, False, matched_exact, resolved_ips)
    - If clean but in /24 neighbor: (False, True, neighbor_subnet, resolved_ips)
    - If completely clean: (False, False, None, resolved_ips)
    - If IPv6 unsupported: (None, False, None, [])
    """
    ensure_rkn_database(cache_dir)

    # Разрешаем хост (домен -> IPv4)
    resolved_ips: list[str] = []
    try:
        ipaddress.IPv4Address(host)
        resolved_ips = [host]
    except ValueError:
        try:
            ipaddress.IPv6Address(host)
            # IPv6 не поддерживается базой IPv4
            return None, False, None, []
        except ValueError:
            # Домен
            try:
                addr_info = socket.getaddrinfo(host, None, socket.AF_INET)
                resolved_ips = list({item[4][0] for item in addr_info})
            except Exception as exc:
                log.warning("Failed resolving domain %s: %s", host, exc)
                return None, False, None, []

    with _CACHE_LOCK:
        if not _EXACT_TREE:
            # Без пригодного кеша возвращаем None (unknown)
            return None, False, None, resolved_ips

        # Проверяем каждый разрешенный IP
        for ip in resolved_ips:
            matched_exact = _match_radix_tree(_EXACT_TREE, ip)
            if matched_exact:
                return True, False, matched_exact, resolved_ips

        for ip in resolved_ips:
            matched_neighbor = _match_radix_tree(_NEIGHBOR_TREE, ip)
            if matched_neighbor:
                return False, True, matched_neighbor, resolved_ips

        return False, False, None, resolved_ips


_REMOTE_PROBE_SCRIPT = """
import socket, time, json

targets = [
    ("77.88.8.8", 53, "Yandex DNS"),
    ("77.88.55.242", 443, "ya.ru"),
    ("87.240.129.133", 443, "vk.com")
]

results = []
for host, port, name in targets:
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=2.5)
        s.close()
        ms = round((time.time() - t0) * 1000)
        results.append({"target": f"{name} ({host}:{port})", "ok": True, "latencyMs": ms})
    except Exception as e:
        results.append({"target": f"{name} ({host}:{port})", "ok": False, "latencyMs": None, "error": str(e)})

print(json.dumps(results))
"""


def probe_ru_reachability(runner: RemoteRunner) -> tuple[str, int | None, list[dict[str, Any]]]:
    """Probe connectivity from target VPS to Russian network infrastructure."""
    b64_script = base64.b64encode(_REMOTE_PROBE_SCRIPT.strip().encode("utf-8")).decode("ascii")
    cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64_script}').decode())\""

    try:
        ret = runner.run(cmd, timeout=8)
        if isinstance(ret, tuple):
            code, stdout, stderr = ret
            out = stdout if code == 0 else ""
            if not out.strip() and stderr.strip():
                raise RuntimeError(stderr.strip()[:200])
        else:
            out = str(ret)
        import json
        details = json.loads(out.strip())
    except Exception as exc:
        log.warning("Failed probing RU reachability on %s: %s", runner.target.host, exc)
        return "unknown", None, [{"target": "probe", "ok": False, "error": str(exc)}]

    successes = [d for d in details if d.get("ok")]
    if not successes:
        return "blocked", None, details

    latencies = [d["latencyMs"] for d in successes if d.get("latencyMs") is not None]
    avg_latency = round(sum(latencies) / len(latencies)) if latencies else None

    if len(successes) == len(details):
        status = "degraded" if avg_latency and avg_latency > 250 else "ok"
    else:
        status = "degraded"

    return status, avg_latency, details


def check_server_censor(
    server_id: str,
    host: str,
    runner: RemoteRunner,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Perform exact 2-tier check: RKN exact list + neighbor detection + live outbound RU probe."""
    exact_blocked, neighbor_warn, matched_subnet, resolved_ips = is_ip_in_rkn_registry(host, cache_dir)
    ru_reachability, avg_latency, details = probe_ru_reachability(runner)

    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result = {
        "rknBlocked": exact_blocked,
        "rknExact": exact_blocked is True,  # Замечание [5]: Соседняя блокировка не помечается как точное совпадение
        "neighborWarning": neighbor_warn,
        "rknSubnet": matched_subnet,
        "resolvedIps": resolved_ips,
        "ruReachability": ru_reachability,
        "ruLatencyMs": avg_latency,
        "details": details,
        "checkedAt": checked_at,
    }

    with _SERVER_RESULTS_LOCK:
        _SERVER_RESULTS_CACHE[server_id] = result

    return result


def get_cached_censor_status(server_id: str) -> dict[str, Any] | None:
    with _SERVER_RESULTS_LOCK:
        return _SERVER_RESULTS_CACHE.get(server_id)
