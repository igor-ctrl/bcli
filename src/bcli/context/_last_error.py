"""Last-error capture for the context bundle (R6).

When the CLI's central error handler catches a :class:`BCLIError`, it
calls :func:`capture_last_error` to drop a small JSON file at
``~/.config/bcli/last-error.json``. ``bcli ask`` reads that file on
the next invocation so a "what happened?" question has the same shape
of evidence ``bcli describe`` would have given.

**No tracebacks by default.** ``last-error.json`` is mode 0644-safe —
it carries no traceback frames. A separate ``last-error-debug.json``
(mode 0600) holds traceback excerpts only when ``--debug`` was active
for that invocation, and the bundle layer excludes it unless
``--include-debug`` is set.

Capturing errors must never raise — the user is already in an error
path; doubling that with a write failure is worse than no capture.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bcli.context._protocol import LastErrorRecord
from bcli.context._redact import redact_text, redact_url

logger = logging.getLogger("bcli.context")

# Filename constants — public so tests + downstream consumers
# (ask, agent) can resolve them without hardcoding strings.
LAST_ERROR_FILENAME = "last-error.json"
LAST_ERROR_DEBUG_FILENAME = "last-error-debug.json"
_SCHEMA_VERSION = "1.0"


def _config_dir() -> Path:
    return Path.home() / ".config" / "bcli"


def last_error_path(*, debug: bool = False) -> Path:
    """Resolve the on-disk path for the captured last-error file.

    ``debug=True`` returns the sibling ``last-error-debug.json`` that
    only exists when the failing invocation was run with ``--debug``.
    """
    return _config_dir() / (
        LAST_ERROR_DEBUG_FILENAME if debug else LAST_ERROR_FILENAME
    )


def capture_last_error(
    *,
    exc: BaseException,
    command: str = "",
    profile: str = "",
    environment: str = "",
    company: str = "",
    debug: bool = False,
    config_dir: Path | None = None,
) -> Path | None:
    """Persist a redacted record of ``exc`` to ``last-error.json``.

    Returns the path of the file we wrote, or ``None`` if writing
    failed silently. ``debug=True`` also writes a sibling
    ``last-error-debug.json`` (mode 0600) that includes a 2KB
    traceback excerpt.

    Best-effort. Never re-raises.
    """
    try:
        target_dir = config_dir or _config_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("could not create config dir for last-error: %s", e)
        return None

    record = _build_record(
        exc=exc,
        command=command,
        profile=profile,
        environment=environment,
        company=company,
        include_traceback=False,
    )
    primary = target_dir / LAST_ERROR_FILENAME
    if not _atomic_write_json(primary, record.__dict__, mode=0o644):
        return None

    if debug:
        debug_record = _build_record(
            exc=exc,
            command=command,
            profile=profile,
            environment=environment,
            company=company,
            include_traceback=True,
        )
        debug_path = target_dir / LAST_ERROR_DEBUG_FILENAME
        _atomic_write_json(debug_path, debug_record.__dict__, mode=0o600)

    return primary


def read_last_error(
    *,
    debug: bool = False,
    config_dir: Path | None = None,
) -> LastErrorRecord | None:
    """Read the captured last-error record back as a typed object.

    Returns ``None`` when the file doesn't exist, is unreadable, or
    fails JSON parse — the bundle layer treats absence as "no recent
    error" rather than crashing.
    """
    target_dir = config_dir or _config_dir()
    path = target_dir / (
        LAST_ERROR_DEBUG_FILENAME if debug else LAST_ERROR_FILENAME
    )
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug("could not read last-error file %s: %s", path, e)
        return None
    if not isinstance(raw, dict):
        return None
    return _record_from_dict(raw)


def _build_record(
    *,
    exc: BaseException,
    command: str,
    profile: str,
    environment: str,
    company: str,
    include_traceback: bool,
) -> LastErrorRecord:
    """Compose a :class:`LastErrorRecord` from an exception.

    Errors carry the BC-specific shape: ``status_code``, ``bc_message``,
    ``correlation_id`` for :class:`bcli.errors.BCLIError` subclasses.
    Other exceptions populate ``error_class`` only — we never reach
    here for non-BCLIError unless ``--debug`` enabled the broader
    capture in app.py.
    """
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    error_class = exc.__class__.__name__
    status = getattr(exc, "status_code", None) or 0
    bc_message = getattr(exc, "bc_message", "") or ""
    correlation_id = getattr(exc, "correlation_id", "") or ""

    # Some BCLIError subclasses (e.g. ValidationError raised by the
    # filter pre-flight) carry a remediation hint on a known attribute;
    # support it gracefully without forcing all of them to add one.
    hint = getattr(exc, "hint", "") or ""
    if not hint and bc_message:
        hint = ""

    url = getattr(exc, "url", "") or ""
    method = getattr(exc, "method", "") or ""
    endpoint = getattr(exc, "endpoint", "") or ""

    # Redact URL query params + free-text BC message in case the BC
    # server happened to echo back a token-shaped string.
    if url:
        url, _ = redact_url(url, location_path="last_error.url")
    if bc_message:
        bc_message, _ = redact_text(
            bc_message, location_path="last_error.bc_message"
        )

    exit_code = _exit_code_for(exc)
    tb_excerpt = ""
    if include_traceback:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        # 2KB cap — enough to cover the topmost frames, capped so the
        # captured file stays small and easy to ship to a model.
        tb_excerpt = tb[-2048:]

    return LastErrorRecord(
        timestamp=timestamp,
        command=command,
        error_class=error_class,
        exit_code=exit_code,
        status=status if isinstance(status, int) else 0,
        profile=profile,
        environment=environment,
        company=company,
        url=url,
        method=method,
        correlation_id=correlation_id,
        endpoint=endpoint,
        hint=hint,
        bc_message=bc_message,
        traceback_excerpt=tb_excerpt,
    )


def _record_from_dict(raw: dict[str, Any]) -> LastErrorRecord:
    """Build a :class:`LastErrorRecord` from a JSON dict.

    Missing fields fall back to dataclass defaults so reading an
    older-schema file still works.
    """
    return LastErrorRecord(
        timestamp=str(raw.get("timestamp", "")),
        command=str(raw.get("command", "")),
        error_class=str(raw.get("error_class", "")),
        exit_code=int(raw.get("exit_code", 0) or 0),
        status=int(raw.get("status", 0) or 0),
        profile=str(raw.get("profile", "")),
        environment=str(raw.get("environment", "")),
        company=str(raw.get("company", "")),
        url=str(raw.get("url", "")),
        method=str(raw.get("method", "")),
        correlation_id=str(raw.get("correlation_id", "")),
        endpoint=str(raw.get("endpoint", "")),
        hint=str(raw.get("hint", "")),
        bc_message=str(raw.get("bc_message", "")),
        traceback_excerpt=str(raw.get("traceback_excerpt", "")),
    )


def _exit_code_for(exc: BaseException) -> int:
    """Best-effort exit code lookup.

    Reuses the AIP §Phase 4c mapping if available; falls back to 1
    when the helper isn't importable (e.g. partial install).
    """
    try:
        from bcli_cli._error_handler import map_error_to_exit_code  # type: ignore
        return int(map_error_to_exit_code(exc))
    except Exception:  # noqa: BLE001
        return 1


def _atomic_write_json(path: Path, payload: dict[str, Any], *, mode: int) -> bool:
    """Atomically write ``payload`` to ``path`` with restrictive mode.

    Returns ``True`` on success, ``False`` on any I/O / serialise
    failure. Never raises — the caller is already in an error path.
    """
    try:
        # Serialise first so a bad payload doesn't trash the previous file.
        data = json.dumps(
            {"schema_version": _SCHEMA_VERSION, **payload},
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            default=str,
        )
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.chmod(tmp_name, mode)
            os.replace(tmp_name, str(path))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except (OSError, TypeError, ValueError) as e:
        logger.debug("could not write last-error file %s: %s", path, e)
        return False


__all__ = [
    "LAST_ERROR_DEBUG_FILENAME",
    "LAST_ERROR_FILENAME",
    "capture_last_error",
    "last_error_path",
    "read_last_error",
]
