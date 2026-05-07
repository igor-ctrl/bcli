"""Opt-in audit log for bcli write operations.

>>> from bcli.audit import get_audit_sink, AuditEntry
>>> sink = get_audit_sink(config.audit, profile_name="dev")  # NullSink if disabled
>>> sink.emit(AuditEntry(...))
"""

from __future__ import annotations

from bcli.audit._factory import get_audit_sink
from bcli.audit._protocol import (
    AuditEntry,
    AuditSink,
    JSONLAuditSink,
    NullAuditSink,
)
from bcli.audit._redact import REDACTED, redact

__all__ = [
    "AuditEntry",
    "AuditSink",
    "JSONLAuditSink",
    "NullAuditSink",
    "REDACTED",
    "get_audit_sink",
    "redact",
]
