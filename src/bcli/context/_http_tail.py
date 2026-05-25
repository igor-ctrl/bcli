"""Rolling NDJSON tail of recent ``bcli.http`` events (R5/R6 support).

Wires a :class:`logging.handlers.RotatingFileHandler` onto the
``bcli.http`` logger so the most recent ~200 HTTP requests land in
``~/.config/bcli/http-tail.ndjson`` for the context bundler to read.
Off by default — opt-in via ``[context] tail = true`` so users on
read-only home dirs (CI, ephemeral containers) don't accidentally
fail boot.

Why NDJSON over a structured log: ``bcli.http`` already emits one
JSON record per line via :mod:`bcli.client._transport`, so the
handler is a trivial passthrough. ``read_http_tail`` parses each line
back into a :class:`HttpEvent` for the bundle.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bcli.context._protocol import HttpEvent
from bcli.context._redact import redact_url

logger = logging.getLogger("bcli.context")

# Filename + size cap are public so tests can assert directly.
HTTP_TAIL_FILENAME = "http-tail.ndjson"
# ~200 events at ~1KB each. Two backups so a rollover keeps the tail
# of the *previous* invocation around for "what just happened?" runs.
DEFAULT_MAX_BYTES = 200_000
DEFAULT_BACKUP_COUNT = 1

_HANDLER_FLAG = "_bcli_context_tail"


def _config_dir() -> Path:
    return Path.home() / ".config" / "bcli"


def http_tail_path(config_dir: Path | None = None) -> Path:
    """Resolve the on-disk path of the rolling tail file."""
    return (config_dir or _config_dir()) / HTTP_TAIL_FILENAME


def enable_http_tail(
    *,
    config_dir: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> bool:
    """Attach the rotating handler to ``bcli.http``.

    Idempotent — subsequent calls noop if the handler is already
    installed (the handler instance carries a ``_bcli_context_tail``
    sentinel attribute).

    Returns ``True`` on success, ``False`` when the target directory
    isn't writable. Never raises.
    """
    try:
        target_dir = config_dir or _config_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("http-tail dir unwritable: %s", e)
        return False

    http_logger = logging.getLogger("bcli.http")

    for h in http_logger.handlers:
        if getattr(h, _HANDLER_FLAG, False):
            return True  # already installed

    try:
        handler = logging.handlers.RotatingFileHandler(
            filename=str(target_dir / HTTP_TAIL_FILENAME),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    except OSError as e:
        logger.debug("could not open http-tail handler: %s", e)
        return False

    # Mark as ours so we don't re-attach.
    setattr(handler, _HANDLER_FLAG, True)

    # Transport already emits structured JSON via its formatter; we
    # use a passthrough format that prints the message body as-is.
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(logging.INFO)
    http_logger.addHandler(handler)
    # Ensure the records actually flow even though the root logger
    # has no handler in normal runtime.
    if http_logger.level == logging.NOTSET or http_logger.level > logging.INFO:
        http_logger.setLevel(logging.INFO)
    return True


def read_http_tail(
    *,
    config_dir: Path | None = None,
    limit: int = 50,
    redact: bool = True,
) -> tuple[HttpEvent, ...]:
    """Read the most recent NDJSON events back as typed records.

    Returns at most ``limit`` events, newest last (chronological).
    Lines that fail to parse are skipped silently — a corrupt line
    must not prevent the bundle from being built.
    """
    path = http_tail_path(config_dir)
    if not path.is_file():
        return ()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.debug("could not read http-tail %s: %s", path, e)
        return ()

    lines = [ln for ln in text.splitlines() if ln.strip()]
    # Keep newest N — older lines get rotated to the .1 file anyway.
    selected = lines[-limit:]

    events: list[HttpEvent] = []
    for raw in selected:
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        events.append(_event_from_dict(obj, redact=redact))
    return tuple(events)


def _event_from_dict(obj: dict[str, Any], *, redact: bool) -> HttpEvent:
    """Coerce a single NDJSON record into :class:`HttpEvent`.

    ``obj`` is whatever ``bcli.client._transport`` emitted. We only
    look up the canonical keys; missing fields default to empty.
    """
    url = str(obj.get("url", ""))
    if redact and url:
        url, _ = redact_url(url, location_path="http_tail.url")
    timestamp = str(
        obj.get("timestamp")
        or obj.get("ts")
        or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    return HttpEvent(
        timestamp=timestamp,
        method=str(obj.get("method", "")),
        url=url,
        status=int(obj.get("status", 0) or 0),
        latency_ms=float(obj.get("latency_ms", 0.0) or 0.0),
        correlation_id=str(obj.get("correlation_id", "")),
        endpoint=str(obj.get("endpoint", "")),
        retry_count=int(obj.get("retry_count", 0) or 0),
    )


__all__ = [
    "DEFAULT_BACKUP_COUNT",
    "DEFAULT_MAX_BYTES",
    "HTTP_TAIL_FILENAME",
    "enable_http_tail",
    "http_tail_path",
    "read_http_tail",
]
