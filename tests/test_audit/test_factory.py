"""Tests for ``get_audit_sink`` — the factory that builds the audit sink
from an ``AuditConfig``.

Disabled config returns ``NullAuditSink``. Enabled config with a path
returns ``JSONLAuditSink``. Path supports ``{profile}`` interpolation so
the same global config produces a per-profile log automatically.
"""

from __future__ import annotations

from bcli.audit._factory import get_audit_sink
from bcli.audit._protocol import JSONLAuditSink, NullAuditSink
from bcli.config._model import AuditConfig


def test_disabled_returns_null_sink() -> None:
    cfg = AuditConfig(enabled=False)
    sink = get_audit_sink(cfg, profile_name="dev")
    assert isinstance(sink, NullAuditSink)


def test_enabled_default_returns_jsonl_sink(tmp_path, monkeypatch) -> None:
    """Default backend is jsonl; with no explicit path it falls back to
    the documented default under XDG config dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = AuditConfig(enabled=True)
    sink = get_audit_sink(cfg, profile_name="dev")
    assert isinstance(sink, JSONLAuditSink)


def test_path_template_substitutes_profile_name(tmp_path) -> None:
    cfg = AuditConfig(
        enabled=True,
        path=str(tmp_path / "audit-{profile}.jsonl"),
    )
    sink = get_audit_sink(cfg, profile_name="prod")
    assert isinstance(sink, JSONLAuditSink)
    assert sink.path == tmp_path / "audit-prod.jsonl"


def test_explicit_path_without_template_used_as_is(tmp_path) -> None:
    target = tmp_path / "shared-audit.jsonl"
    cfg = AuditConfig(enabled=True, path=str(target))
    sink = get_audit_sink(cfg, profile_name="dev")
    assert isinstance(sink, JSONLAuditSink)
    assert sink.path == target


def test_unknown_backend_falls_back_to_null(tmp_path) -> None:
    """A misspelled backend value must NOT crash the CLI — it falls back
    to the NullAuditSink and logs a warning."""
    cfg = AuditConfig(enabled=True, backend="not-a-real-backend")
    sink = get_audit_sink(cfg, profile_name="dev")
    assert isinstance(sink, NullAuditSink)


def test_max_size_propagates_to_sink(tmp_path) -> None:
    cfg = AuditConfig(
        enabled=True,
        path=str(tmp_path / "a.jsonl"),
        max_size_mb=5,
    )
    sink = get_audit_sink(cfg, profile_name="dev")
    assert isinstance(sink, JSONLAuditSink)
    assert sink.max_size_bytes == 5 * 1024 * 1024


def test_none_config_returns_null_sink() -> None:
    """Profiles without an [audit] section get a no-op sink."""
    sink = get_audit_sink(None, profile_name="dev")
    assert isinstance(sink, NullAuditSink)
