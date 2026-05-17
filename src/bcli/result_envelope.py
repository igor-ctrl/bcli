"""Mutation result envelope (AIP v0.1 §Phase 2).

The envelope is a single JSON object an agent runtime can consume on a
side channel — never on stdout. Every mutating CLI verb (`post`, `patch`,
`delete`, `attach upload`, `batch run`) emits one envelope per invocation
when the user passes ``--result-out PATH`` or ``--result-fd N``.

Path mode writes atomically (tmp + ``os.replace``) so a SIGKILL between
write and rename never leaves a half-written file on the documented
output path. Fd mode writes the JSON object then closes the descriptor
so a pipe reader can see EOF.

Stdout output is untouched: the existing ``--format`` flag still drives
whatever the human/CSV/JSON dump looks like. The envelope is the
*attestation* (profile, target, correlation id, outcome), not the
response body.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

ENVELOPE_VERSION = "0.1"


@dataclass(frozen=True)
class ResultEnvelope:
    """One mutation, attested.

    Fields mirror the contract doc table in §Phase 2. ``record_id`` is
    extracted from the response body on success (``systemId``/``id``/first
    ``*Id``) when available, ``None`` otherwise. ``telemetry_event_id`` and
    ``audit_log_offset`` are currently always ``None`` — wiring them needs
    a protocol extension on the telemetry + audit sinks, deferred to a
    follow-up so this PR stays additive.
    """

    version: str
    invocation_id: str
    tool_version: str
    profile: str | None
    environment: str | None
    company: str | None
    method: str
    endpoint: str
    resolved_url: str | None
    record_id: str | None
    dry_run: bool
    status: str  # "succeeded" | "failed"
    exit_code: int
    bc_correlation_id: str | None
    telemetry_event_id: str | None  # TODO: wire via TelemetrySink follow-up
    audit_log_offset: int | None    # TODO: wire via AuditSink follow-up
    started_at: str  # ISO 8601 UTC
    duration_ms: int


def write_envelope(
    envelope: ResultEnvelope,
    *,
    path: Optional[Path] = None,
    fd: Optional[int] = None,
) -> None:
    """Serialize ``envelope`` to ``path`` (atomic) or ``fd`` (write+close).

    Exactly one of ``path`` / ``fd`` must be provided. Path mode creates
    parent directories if missing and uses ``os.replace`` for atomicity.
    Fd mode writes the JSON and closes the descriptor so a downstream pipe
    reader sees EOF.
    """
    if path is not None and fd is not None:
        raise ValueError("write_envelope: pass either path or fd, not both")
    if path is None and fd is None:
        raise ValueError("write_envelope: must pass either path or fd")

    payload = json.dumps(asdict(envelope), default=str, indent=2)

    if path is not None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # NamedTemporaryFile in the same dir so os.replace is on one FS.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=target.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name
        os.replace(tmp_name, str(target))
        return

    # fd path
    assert fd is not None
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


__all__ = ["ENVELOPE_VERSION", "ResultEnvelope", "write_envelope"]
