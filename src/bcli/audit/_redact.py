"""Key-based redaction for audit log payloads.

Walks dicts and lists, replacing values whose key contains any of the
configured tokens (case-insensitive substring match) with the sentinel
``REDACTED``. Non-dict bodies pass through unchanged. Never mutates the
input — returns a deep-copied structure.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

REDACTED = "***REDACTED***"


def redact(value: Any, keys: Iterable[str]) -> Any:
    """Return a copy of ``value`` with sensitive values replaced.

    A field is redacted if any token in ``keys`` appears (case-insensitive)
    anywhere in the field name. So ``("token",)`` matches ``token``,
    ``apiToken``, ``session_token``, ``Token`` — all of them. This is
    deliberately wide: false positives in an audit log are cheap, false
    negatives leak credentials.
    """
    needles = tuple(k.lower() for k in keys if k)
    if not needles:
        return _deep_copy(value)
    return _walk(value, needles)


def _walk(value: Any, needles: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _matches(k, needles):
                out[k] = REDACTED
            else:
                out[k] = _walk(v, needles)
        return out
    if isinstance(value, list):
        return [_walk(item, needles) for item in value]
    if isinstance(value, tuple):
        return tuple(_walk(item, needles) for item in value)
    return value


def _matches(key: str, needles: tuple[str, ...]) -> bool:
    lowered = key.lower()
    return any(n in lowered for n in needles)


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_deep_copy(v) for v in value)
    return value
