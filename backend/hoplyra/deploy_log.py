"""Bounded, process-local deployment logs. Only publish curated progress text."""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

_condition = threading.Condition(threading.RLock())
_context = threading.local()


@dataclass
class _Log:
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=2000))
    seq: int = 0
    active: bool = False
    finished: bool = False
    ok: bool | None = None
    updated: float = field(default_factory=time.monotonic)


_logs: dict[str, _Log] = {}


def exists(config_id: str) -> bool:
    with _condition:
        return config_id in _logs


def _redact(message: str) -> str:
    # Redact BEFORE truncating: never retain the beginning of a long key or PEM.
    msg = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
                 "<redacted>", str(message), flags=re.S)

    # 1. Credentials in URIs (socks5://user:pass@host, hysteria2://primary%3Apass@host, vless://uuid@host)
    # Match userinfo greedily up to the @ before host
    msg = re.sub(r"(?i)\b([a-z0-9+.-]+://)[^\s/]+@", r"\1<redacted>@", msg)

    # 2. Multi-line YAML auth (e.g. auth:\n  userpass:\n    primary: "...")
    # Safe O(N) pattern with no catastrophic backtracking
    msg = re.sub(
        r"(?im)(^\s*auth\s*:\s*\r?\n)(?:[ \t]+[^ \t\r\n][^\r\n]*\r?\n)*?[ \t]*(?:primary|password|secret)\s*:\s*[^\r\n]*",
        r"\1  <redacted>",
        msg,
    )

    # 3. Sensitive assignments and CLI flags (require = or : for common words token/secret to not cut normal english text)
    msg = re.sub(
        r"(?im)(\b(?:private[_-]?key|preshared[_-]?key|psk|password|passwd|passphrase|auth_secret|headerProtectionKey|SSHPASS)\b[\"']?\s*(?:[=:]\s*|\s+)|\b(?:token|secret)\b[\"']?\s*(?:[=:]\s*)|\bpass\s*=\s*|--(?:pass(?:word)?|token|secret)(?:=|\s+)|\bauth\s*:\s*).*?$",
        r"\1<redacted>", msg,
    )

    # 4. pinSHA256 is a public certificate fingerprint, not credential material.
    # Unlabeled standard base64 and base64url keys (40+ chars) - do not combine / and - to avoid matching paths with UUIDs
    msg = re.sub(
        r"(?i)(pinSHA256\s*[=:]\s*)([A-Za-z0-9+/=_-]{40,})|([A-Za-z0-9+/=]{40,}|[A-Za-z0-9_=-]{40,})",
        lambda m: m.group(1) + m.group(2) if m.group(1) else "<redacted>",
        msg,
    )
    return msg[:500]


def publish(config_id: str, message: str, *, level: str = "info", stage: str | None = None) -> None:
    redacted_message = _redact(message)
    redacted_stage = _redact(stage) if stage is not None else None
    lvl = level if level in {"info", "warn", "error", "success"} else "info"
    with _condition:
        record = _logs.setdefault(config_id, _Log())
        record.seq += 1
        record.updated = time.monotonic()
        record.events.append({"seq": record.seq, "ts": time.time(),
                              "level": lvl,
                              "stage": redacted_stage,
                              "message": redacted_message})
        _condition.notify_all()


def snapshot(config_id: str, since_seq: int = 0) -> list[dict[str, Any]]:
    with _condition:
        record = _logs.get(config_id)
        return [dict(event) for event in record.events if event["seq"] > since_seq] if record else []


def status(config_id: str) -> dict[str, Any]:
    with _condition:
        record = _logs.get(config_id)
        return {"active": bool(record and record.active), "finished": bool(record and record.finished),
                "ok": record.ok if record else None, "seq": record.seq if record else 0}


def read(config_id: str, since_seq: int = 0) -> dict[str, Any]:
    """Read events and their matching status atomically (including the final event)."""
    with _condition:
        return {"events": snapshot(config_id, since_seq), "status": status(config_id)}


def mark_active(config_id: str) -> None:
    with _condition:
        record = _logs.setdefault(config_id, _Log())
        record.active, record.finished, record.ok = True, False, None
        _condition.notify_all()


def mark_finished(config_id: str, ok: bool) -> None:
    with _condition:
        record = _logs.setdefault(config_id, _Log())
        record.active, record.finished, record.ok = False, True, ok
        record.updated = time.monotonic()
        _condition.notify_all()
    prune()


def wait_for_events(config_id: str, since_seq: int, timeout: float) -> list[dict[str, Any]]:
    with _condition:
        _condition.wait_for(lambda: status(config_id)["seq"] > since_seq or status(config_id)["finished"], timeout)
        return snapshot(config_id, since_seq)


def clear(config_id: str) -> None:
    with _condition:
        _logs.pop(config_id, None)
        _condition.notify_all()


def prune(max_configs: int = 50) -> None:
    with _condition:
        finished = sorted((key for key, value in _logs.items() if value.finished),
                          key=lambda key: _logs[key].updated)
        for key in finished[:max(0, len(_logs) - max_configs)]:
            del _logs[key]


@contextmanager
def deploy_context(config_id: str) -> Iterator[None]:
    """Bind remote progress to the job thread, never to a global current config."""
    previous = getattr(_context, "config_id", None)
    _context.config_id = config_id
    try:
        yield
    finally:
        _context.config_id = previous


def publish_current(message: str, *, stage: str | None = None, level: str = "info") -> None:
    config_id = getattr(_context, "config_id", None)
    if config_id:
        publish(config_id, message, stage=stage, level=level)
