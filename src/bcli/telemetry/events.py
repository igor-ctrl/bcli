"""Event factories for bcli telemetry — privacy-preserving by default.

Each factory returns a ``(name, properties)`` tuple ready to hand to
:meth:`bcli.telemetry.TelemetrySink.emit`. All values in ``properties`` are
flat primitives so they survive the OpenTelemetry attribute serialiser
unchanged.

The factories never include access tokens, client secrets, raw record
IDs, or full filter strings. ``filter_text`` and ``user_upn`` are
opt-in and only included when the caller passes a non-empty value
(callers are expected to consult :attr:`TelemetryConfig.capture_filter_text`
and :attr:`TelemetryConfig.capture_user_upn` before doing so).
"""

from __future__ import annotations

import platform
import re
import sys
from typing import Any

from bcli._version import __version__

EventTuple = tuple[str, dict[str, Any]]


# ─── Factories ────────────────────────────────────────────────────────


def startup(
    *,
    profile: str,
    environment: str = "",
    command: str = "",
) -> EventTuple:
    """Emitted once per CLI invocation — useful for adoption / DAU metrics."""
    return "bcli.startup", {
        "version": __version__,
        "profile": profile,
        "environment": environment,
        "command": command,
        "os": platform.system(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def command(
    *,
    command: str,
    profile: str,
    environment: str = "",
    company_alias: str = "",
    duration_ms: float = 0.0,
    status: str = "ok",
) -> EventTuple:
    """Emitted at the end of every CLI command (success or error)."""
    return "bcli.command", {
        "command": command,
        "profile": profile,
        "environment": environment,
        "company_alias": company_alias,
        "duration_ms": float(duration_ms),
        "status": status,
    }


def query(
    *,
    endpoint: str,
    has_filter: bool,
    select_count: int = 0,
    top: int = -1,
    skip: int = -1,
    all_pages: bool = False,
    status: int = 0,
    latency_ms: float = 0.0,
    correlation_id: str = "",
    filter_text: str = "",
) -> EventTuple:
    """Emitted per OData GET. ``filter_text`` is OPT-IN only.

    Callers must check ``config.telemetry.capture_filter_text`` before
    passing the literal filter; pass ``""`` to omit it.
    """
    props: dict[str, Any] = {
        "endpoint": endpoint,
        "has_filter": bool(has_filter),
        "select_count": int(select_count),
        "top": int(top),
        "skip": int(skip),
        "all_pages": bool(all_pages),
        "status": int(status),
        "latency_ms": float(latency_ms),
        "correlation_id": correlation_id,
    }
    if filter_text:
        props["filter_text"] = filter_text
    return "bcli.query", props


def auth(
    *,
    method: str,
    status: str,
    user_upn: str = "",
) -> EventTuple:
    """Emitted on auth attempts. Never logs tokens or secrets.

    ``user_upn`` is OPT-IN — caller must check
    ``config.telemetry.capture_user_upn`` before passing it.
    """
    props: dict[str, Any] = {"method": method, "status": status}
    if user_upn:
        props["user_upn"] = user_upn
    return "bcli.auth", props


def error(
    *,
    error_class: str,
    http_status: int = 0,
    bc_message: str = "",
    correlation_id: str = "",
    endpoint: str = "",
) -> EventTuple:
    """Emitted on CLI errors. ``bc_message`` is sanitised for token-shaped substrings."""
    return "bcli.error", {
        "error_class": error_class,
        "http_status": int(http_status),
        "bc_message": _sanitise(bc_message),
        "correlation_id": correlation_id,
        "endpoint": endpoint,
    }


# ─── Sanitiser ───────────────────────────────────────────────────────


# Patterns that look like tokens / secrets, redacted before they leave
# the laptop. Best-effort — defence in depth on top of the schema-level
# privacy controls.
_SECRET_PATTERNS = [
    r"Bearer\s+[A-Za-z0-9._\-]+",
    r"\bey[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",  # JWT
    r"\bsk_(?:live|test)_[A-Za-z0-9_\-]+",
    r"InstrumentationKey=[A-Za-z0-9\-]+",
    r"\b[a-fA-F0-9]{40,}\b",  # long hex tokens
]
_SECRET_RE = re.compile("|".join(_SECRET_PATTERNS))

_MAX_MESSAGE_LEN = 500


def _sanitise(text: str) -> str:
    """Best-effort redaction of token-shaped substrings. Caps length too."""
    if not text:
        return ""
    redacted = _SECRET_RE.sub("[REDACTED]", text)
    if len(redacted) > _MAX_MESSAGE_LEN:
        redacted = redacted[:_MAX_MESSAGE_LEN] + "…"
    return redacted
