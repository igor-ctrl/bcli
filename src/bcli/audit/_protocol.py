"""Audit-sink protocol + built-in NullAuditSink and JSONLAuditSink.

The audit log is opt-in (off by default). When enabled, every CLI write
appends one JSONL line to a per-profile file. The file rotates once it
crosses ``max_size_bytes`` — the previous content moves to ``<path>.1``
and a fresh file is started. Only one backup is kept; users who need
indefinite retention should ship the file to their own log store.

A sink MUST never crash the CLI. Path resolution failure, write failure,
disk full, permissions error — all swallowed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("bcli.audit")


@dataclass(frozen=True)
class AuditEntry:
    """One row in the audit log.

    Field semantics:

    * ``ts``                 — ISO 8601 UTC timestamp of the request.
    * ``profile``            — active profile name.
    * ``environment``        — BC environment (e.g. ``Production``).
    * ``company_id``         — BC company id; ``None`` for company-less calls.
    * ``method``             — HTTP method (POST / PATCH / DELETE / UPLOAD).
    * ``endpoint``           — entity-set name passed to ``bcli``.
    * ``resolved_url``       — full URL that was (or would have been) hit.
    * ``record_id``          — for PATCH / DELETE; ``None`` otherwise.
    * ``request_body``       — redacted body that was sent; ``None`` for DELETE.
    * ``status``             — HTTP status code; ``None`` if the call never
                                fired (dry-run, pre-call failure).
    * ``correlation_id``     — BC ``x-ms-correlation-request-id`` header; useful
                                when grepping BC server-side logs.
    * ``latency_ms``         — round-trip latency; ``None`` if no call fired.
    * ``cli_version``        — bcli version string.
    * ``caller``             — ``cli`` | ``mcp`` | ``sdk``; who initiated.
    * ``outcome``            — ``completed`` | ``failed`` | ``dry_run``.
    * ``error``              — exception message; ``None`` on success.
    """

    ts: str
    profile: str
    environment: str
    company_id: str | None
    method: str
    endpoint: str
    resolved_url: str | None
    record_id: str | None
    request_body: Any | None
    status: int | None
    correlation_id: str | None
    latency_ms: int | None
    cli_version: str
    caller: str
    outcome: str
    error: str | None = None


@runtime_checkable
class AuditSink(Protocol):
    """Structural type for audit sinks."""

    is_active: bool

    def emit(self, entry: AuditEntry) -> None: ...


class NullAuditSink:
    """Zero-overhead sink. Returned when audit is disabled or misconfigured."""

    is_active: bool = False

    def emit(self, entry: AuditEntry) -> None:  # noqa: ARG002
        return None


class JSONLAuditSink:
    """Append-only JSONL file sink with single-backup rotation.

    Rotation strategy: when the file exceeds ``max_size_bytes`` BEFORE a
    write, the existing file is moved to ``<path>.1`` (overwriting any
    prior backup) and a fresh file takes its place. This bounds disk usage
    at ``2 * max_size_bytes`` regardless of how many writes happen.
    """

    is_active: bool = True

    def __init__(self, path: Path, max_size_bytes: int = 50 * 1024 * 1024) -> None:
        self.path = path
        self.max_size_bytes = max_size_bytes

    def emit(self, entry: AuditEntry) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            line = json.dumps(asdict(entry), default=str)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:  # noqa: BLE001 — sink must never raise
            logger.debug("audit emit failed: %s", exc)

    def _rotate_if_needed(self) -> None:
        if not self.path.is_file():
            return
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.max_size_bytes:
            return
        backup = self.path.with_suffix(self.path.suffix + ".1")
        try:
            if backup.exists():
                backup.unlink()
            self.path.rename(backup)
        except OSError as exc:
            logger.debug("audit rotate failed: %s", exc)
