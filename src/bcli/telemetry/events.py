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

Every event carries a small set of *common dimensions* (version, OS,
arch, install_id) so that admins running KQL against a shared App
Insights resource can filter junk traffic out of real-laptop traffic
without needing per-event tagging at the call sites. See
:func:`_common_dims` and :func:`_install_id`.
"""

from __future__ import annotations

import logging
import platform
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from bcli._version import __version__
# Re-exported so ``_install_id`` and tests can both reach the path via
# ``bcli.telemetry.events.INSTALL_ID_FILE``. Ruff can't see the indirect
# use through ``getattr(self_module, "INSTALL_ID_FILE")`` style lookup.
from bcli.config._defaults import INSTALL_ID_FILE  # noqa: F401

logger = logging.getLogger(__name__)

EventTuple = tuple[str, dict[str, Any]]


# ─── Common dimensions (added to every event) ────────────────────────


_install_id_cache: str | None = None


def _install_id(path: Path | None = None) -> str:
    """Return a stable per-laptop UUID, generating it on first call.

    The file lives at ``~/.config/bcli/install-id`` and contains a single
    random UUID4. It is plaintext on purpose — there's no PII or secret
    inside, only an opaque identifier used by KQL queries to distinguish
    "real laptop" traffic from "someone abusing our connection string."

    ``path`` is resolved from the *current* module-level
    ``INSTALL_ID_FILE`` when omitted, so monkeypatching it in tests
    actually takes effect (a default-arg lookup would freeze the value
    at definition time).

    Robust against:

    * First-run (no file yet) → generates and writes one.
    * Corrupt / truncated file → silently regenerates.
    * Read-only home dir or any I/O error → returns ``"unknown"`` so
      telemetry never crashes the CLI on this code path.
    """
    global _install_id_cache
    if _install_id_cache is not None:
        return _install_id_cache

    if path is None:
        # Re-resolve from the module attribute on every call so test
        # monkeypatches actually land here. Caching above means we only
        # look up once per process anyway.
        from bcli.telemetry import events as _self_module
        path = _self_module.INSTALL_ID_FILE

    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            # UUIDs are 36 chars including dashes; longer/shorter is corrupt.
            if 32 <= len(text) <= 64:
                try:
                    parsed = uuid.UUID(text)
                    _install_id_cache = str(parsed)
                    return _install_id_cache
                except ValueError:
                    pass  # fall through to regen

        # Generate + persist. Best-effort write; if it fails we still
        # return the in-memory id for this process so events stay tagged.
        new_id = str(uuid.uuid4())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_id, encoding="utf-8")
        except OSError as e:
            logger.debug("Could not persist install-id to %s: %s", path, e)
        _install_id_cache = new_id
        return _install_id_cache
    except OSError as e:
        logger.debug("install-id load failed (%s); using 'unknown'.", e)
        _install_id_cache = "unknown"
        return _install_id_cache


def _common_dims() -> dict[str, Any]:
    """Dimensions added to every emitted event.

    Cheap to compute (all values are static or cached after first call),
    safe (no PII, no secrets), and useful for KQL: lets an admin filter
    by ``version`` to ignore stale clients during a rollout, by ``os`` to
    spot platform-specific bugs, and by ``install_id`` to bucket traffic
    into real-laptop cohorts.
    """
    return {
        "version": __version__,
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "install_id": _install_id(),
    }


def reset_install_id_cache() -> None:
    """Test-only: drop the in-process install-id memo."""
    global _install_id_cache
    _install_id_cache = None


# ─── Factories ────────────────────────────────────────────────────────


def startup(
    *,
    profile: str,
    environment: str = "",
    command: str = "",
) -> EventTuple:
    """Emitted once per CLI invocation — useful for adoption / DAU metrics."""
    props = _common_dims()
    props.update({
        "profile": profile,
        "environment": environment,
        "command": command,
    })
    return "bcli.startup", props


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
    props = _common_dims()
    props.update({
        "command": command,
        "profile": profile,
        "environment": environment,
        "company_alias": company_alias,
        "duration_ms": float(duration_ms),
        "status": status,
    })
    return "bcli.command", props


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
    props = _common_dims()
    props.update({
        "endpoint": endpoint,
        "has_filter": bool(has_filter),
        "select_count": int(select_count),
        "top": int(top),
        "skip": int(skip),
        "all_pages": bool(all_pages),
        "status": int(status),
        "latency_ms": float(latency_ms),
        "correlation_id": correlation_id,
    })
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
    props = _common_dims()
    props.update({"method": method, "status": status})
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
    props = _common_dims()
    props.update({
        "error_class": error_class,
        "http_status": int(http_status),
        "bc_message": _sanitise(bc_message),
        "correlation_id": correlation_id,
        "endpoint": endpoint,
    })
    return "bcli.error", props


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
