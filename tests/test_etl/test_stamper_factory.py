"""ETL stamper plugin discovery + resolution (bcli.etl.stampers group)."""

from __future__ import annotations

import pytest

pytest.importorskip("dlt")

from bcli.etl import _stamper_factory as mod
from bcli.etl._stamper_factory import build_stampers, discover_stamper_factories


def _fake_audit_factory():
    def _stamp(page):
        return [{**rec, "_synced": "ts", "_deleted": False} for rec in page]

    return _stamp


def _broken_factory():
    raise RuntimeError("boom")


def _patch_factories(monkeypatch, mapping):
    """Force discover_stamper_factories() to return our test mapping."""
    monkeypatch.setattr(mod, "discover_stamper_factories", lambda: dict(mapping))


def test_empty_names_is_noop(monkeypatch):
    _patch_factories(monkeypatch, {})
    assert build_stampers([]) == []


def test_resolves_named_stamper_in_order(monkeypatch):
    def _a():
        return lambda page: [{**r, "a": 1} for r in page]

    def _b():
        return lambda page: [{**r, "b": 2} for r in page]

    _patch_factories(monkeypatch, {"a": _a, "b": _b})
    stampers = build_stampers(["b", "a"])
    assert len(stampers) == 2
    # Applied in the requested order: b then a.
    out = stampers[0]([{"id": "1"}])
    assert out[0]["b"] == 2


def test_audit_style_plugin(monkeypatch):
    _patch_factories(monkeypatch, {"audit": _fake_audit_factory})
    stampers = build_stampers(["audit"])
    out = stampers[0]([{"id": "1"}])
    assert out[0]["_synced"] == "ts"
    assert out[0]["_deleted"] is False


def test_unknown_name_skipped_with_warning(monkeypatch, caplog):
    _patch_factories(monkeypatch, {"known": _fake_audit_factory})
    with caplog.at_level("WARNING"):
        stampers = build_stampers(["nope"])
    assert stampers == []
    assert any("not registered" in r.message for r in caplog.records)


def test_failing_factory_skipped(monkeypatch, caplog):
    _patch_factories(monkeypatch, {"broken": _broken_factory})
    with caplog.at_level("WARNING"):
        stampers = build_stampers(["broken"])
    assert stampers == []
    assert any("raised" in r.message for r in caplog.records)


def test_discover_skips_non_callable(monkeypatch):
    """A non-callable entry point is dropped, not returned."""

    class _FakeEP:
        name = "bad"

        def load(self):
            return "not-callable"

    monkeypatch.setattr(mod, "_iter_entrypoints", lambda: iter([_FakeEP()]))
    assert discover_stamper_factories() == {}
