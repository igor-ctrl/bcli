"""Tests for the JSONL audit-log sink.

The sink appends one JSON object per write operation to a file the user
points at via ``[audit] path``. Rotation is size-based: when the file
exceeds ``max_size_mb`` the previous content moves to ``<path>.1`` and a
fresh file is started. Only one backup is kept (the audit log is for
recent forensic context, not historical archival — that's the user's
log-shipping job).
"""

from __future__ import annotations

import json
from pathlib import Path

from bcli.audit._protocol import (
    AuditEntry,
    JSONLAuditSink,
    NullAuditSink,
)


def _entry(**overrides) -> AuditEntry:
    base = {
        "ts": "2026-05-06T10:00:00Z",
        "profile": "dev",
        "environment": "Sandbox",
        "company_id": "c-123",
        "method": "POST",
        "endpoint": "customers",
        "resolved_url": "https://example.test/api/v2.0/companies(c-123)/customers",
        "record_id": None,
        "request_body": {"displayName": "Test"},
        "status": 201,
        "correlation_id": "abc-cor",
        "latency_ms": 312,
        "cli_version": "0.2.0",
        "caller": "cli",
        "outcome": "completed",
        "error": None,
    }
    base.update(overrides)
    return AuditEntry(**base)


class TestNullAuditSink:
    def test_is_active_false(self) -> None:
        sink = NullAuditSink()
        assert sink.is_active is False

    def test_emit_is_a_noop(self) -> None:
        sink = NullAuditSink()
        sink.emit(_entry())  # must not raise


class TestJSONLAuditSink:
    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "audit.jsonl"
        sink = JSONLAuditSink(path=target)
        sink.emit(_entry())
        assert target.is_file()

    def test_emit_writes_one_jsonl_line(self, tmp_path: Path) -> None:
        target = tmp_path / "audit.jsonl"
        sink = JSONLAuditSink(path=target)
        sink.emit(_entry(method="POST", endpoint="customers"))
        sink.emit(_entry(method="DELETE", endpoint="items", record_id="x"))

        lines = target.read_text().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["method"] == "POST"
        assert first["endpoint"] == "customers"
        second = json.loads(lines[1])
        assert second["method"] == "DELETE"
        assert second["record_id"] == "x"

    def test_jsonl_payload_round_trips_through_json(self, tmp_path: Path) -> None:
        """Every emitted line must be parseable JSON with stable shape."""
        target = tmp_path / "audit.jsonl"
        sink = JSONLAuditSink(path=target)
        sink.emit(_entry())
        line = target.read_text().splitlines()[0]
        payload = json.loads(line)

        # Every documented field present
        for key in (
            "ts", "profile", "environment", "company_id", "method",
            "endpoint", "resolved_url", "request_body", "status",
            "correlation_id", "latency_ms", "cli_version", "caller",
            "outcome",
        ):
            assert key in payload

    def test_rotation_keeps_one_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "audit.jsonl"
        sink = JSONLAuditSink(path=target, max_size_bytes=200)

        sink.emit(_entry())
        size_one_entry = target.stat().st_size

        # Now write many more — should rotate repeatedly, but disk usage
        # must stay bounded (one current + one backup, max).
        for _ in range(100):
            sink.emit(_entry())

        backup = target.with_suffix(".jsonl.1")
        assert target.is_file()
        assert backup.is_file()

        # Bounded: current + backup never exceeds 2 entries' worth of
        # slack on top of the threshold. Constant in the number of writes.
        total = target.stat().st_size + backup.stat().st_size
        assert total <= 2 * size_one_entry + 200, (
            f"unbounded growth: {total} bytes after 101 writes"
        )

        # Each file individually contains valid JSONL.
        for f in (target, backup):
            for line in f.read_text().splitlines():
                json.loads(line)

    def test_is_active_true(self, tmp_path: Path) -> None:
        sink = JSONLAuditSink(path=tmp_path / "audit.jsonl")
        assert sink.is_active is True

    def test_emit_never_raises_on_unwritable_path(self, tmp_path: Path) -> None:
        """The audit sink must never crash the CLI. If the path is bad,
        the emit should swallow it."""
        # /dev/null/foo is guaranteed to fail mkdir on POSIX.
        target = Path("/dev/null/cant-write-here.jsonl")
        sink = JSONLAuditSink(path=target)
        # Must not raise:
        sink.emit(_entry())
