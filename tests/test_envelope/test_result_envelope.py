"""Tests for the core ``bcli.result_envelope`` module.

Covers the data class shape, atomic write to ``--result-out PATH``, and
write+close semantics for ``--result-fd N``. CLI integration tests live
in ``test_envelope_post.py`` and friends.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path

import pytest

from bcli.result_envelope import (
    ENVELOPE_VERSION,
    ResultEnvelope,
    write_envelope,
)


SPEC_FIELDS = {
    "version",
    "invocation_id",
    "tool_version",
    "profile",
    "environment",
    "company",
    "method",
    "endpoint",
    "resolved_url",
    "record_id",
    "dry_run",
    "status",
    "exit_code",
    "bc_correlation_id",
    "telemetry_event_id",
    "audit_log_offset",
    "started_at",
    "duration_ms",
}


def _make_envelope(**overrides) -> ResultEnvelope:
    base = dict(
        version=ENVELOPE_VERSION,
        invocation_id="inv-1",
        tool_version="0.3.0",
        profile="dev",
        environment="Sandbox",
        company="c-1",
        method="POST",
        endpoint="vendors",
        resolved_url="https://example.test/vendors",
        record_id="vnd-1",
        dry_run=False,
        status="succeeded",
        exit_code=0,
        bc_correlation_id=None,
        telemetry_event_id=None,
        audit_log_offset=None,
        started_at="2026-05-16T00:00:00Z",
        duration_ms=42,
    )
    base.update(overrides)
    return ResultEnvelope(**base)


class TestEnvelopeShape:
    def test_has_all_spec_fields(self):
        names = {f.name for f in fields(ResultEnvelope)}
        assert SPEC_FIELDS.issubset(names), SPEC_FIELDS - names

    def test_envelope_is_frozen_dataclass(self):
        env = _make_envelope()
        with pytest.raises(Exception):
            env.method = "PATCH"  # type: ignore[misc]


class TestAtomicWriteToPath:
    def test_writes_json_file_to_path(self, tmp_path: Path):
        env = _make_envelope()
        out = tmp_path / "out.json"
        write_envelope(env, path=out)
        assert out.is_file()
        loaded = json.loads(out.read_text())
        assert loaded == asdict(env)

    def test_creates_parent_directories(self, tmp_path: Path):
        env = _make_envelope()
        out = tmp_path / "nested" / "dir" / "out.json"
        write_envelope(env, path=out)
        assert out.is_file()
        assert json.loads(out.read_text())["method"] == "POST"

    def test_uses_tmp_plus_rename_atomic(self, tmp_path: Path, monkeypatch):
        """The envelope writer must use ``os.replace`` so a SIGKILL between
        ``write`` and ``rename`` never leaves a half-written file on the
        documented output path.
        """
        env = _make_envelope()
        out = tmp_path / "out.json"

        calls: list[tuple[str, str]] = []
        real_replace = os.replace

        def spy_replace(src, dst):
            calls.append((str(src), str(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr("bcli.result_envelope.os.replace", spy_replace)
        write_envelope(env, path=out)

        assert len(calls) == 1
        src, dst = calls[0]
        assert dst == str(out)
        assert src != str(out)  # tmp file != final path
        assert src.startswith(str(out))  # tmp file colocated with target

    def test_overwrites_existing_envelope(self, tmp_path: Path):
        out = tmp_path / "out.json"
        out.write_text('{"stale": true}')
        write_envelope(_make_envelope(method="PATCH"), path=out)
        loaded = json.loads(out.read_text())
        assert loaded["method"] == "PATCH"


class TestWriteToFd:
    def test_writes_envelope_to_file_descriptor(self):
        r, w = os.pipe()
        try:
            write_envelope(_make_envelope(record_id="vnd-9"), fd=w)
            # write_envelope closes the fd it was handed
            data = os.read(r, 65536)
            loaded = json.loads(data)
            assert loaded["record_id"] == "vnd-9"
        finally:
            os.close(r)
            # fd ``w`` was closed by write_envelope; closing again is fine
            try:
                os.close(w)
            except OSError:
                pass

    def test_fd_is_closed_after_write(self):
        r, w = os.pipe()
        try:
            write_envelope(_make_envelope(), fd=w)
            # subsequent write should fail because fd is closed
            with pytest.raises(OSError):
                os.write(w, b"x")
        finally:
            os.close(r)


class TestArgValidation:
    def test_rejects_both_path_and_fd(self, tmp_path: Path):
        with pytest.raises(ValueError):
            write_envelope(_make_envelope(), path=tmp_path / "x.json", fd=2)

    def test_rejects_neither_path_nor_fd(self):
        with pytest.raises(ValueError):
            write_envelope(_make_envelope())
