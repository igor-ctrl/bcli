"""Tests for the telemetry sink — disabled vs active vs missing-SDK paths."""

from __future__ import annotations

from bcli.config._model import TelemetryConfig
from bcli.telemetry import NullSink, TelemetrySink, get_sink
from bcli.telemetry import events


class TestGetSink:
    def test_returns_null_sink_when_disabled(self):
        cfg = TelemetryConfig()  # default: enabled=False
        sink = get_sink(cfg)
        assert isinstance(sink, NullSink)
        assert sink.is_active is False

    def test_returns_null_sink_when_enabled_but_no_connection_string(self):
        cfg = TelemetryConfig(enabled=True, connection_string="")
        sink = get_sink(cfg)
        assert isinstance(sink, NullSink), "missing connection_string must fall back to NullSink"

    def test_returns_real_sink_when_active(self):
        cfg = TelemetryConfig(enabled=True, connection_string="InstrumentationKey=fake;")
        sink = get_sink(cfg)
        assert isinstance(sink, TelemetrySink)
        assert sink.is_active is True

    def test_returns_null_sink_when_config_is_none(self):
        sink = get_sink(None)
        assert isinstance(sink, NullSink)


class TestNullSink:
    def test_emit_is_noop(self):
        sink = NullSink()
        # Must not raise no matter what's passed
        sink.emit("anything", {"x": 1, "secret": "very_real"})

    def test_flush_is_noop(self):
        NullSink().flush()


class TestTelemetryConfigIsActive:
    def test_disabled_is_inactive(self):
        cfg = TelemetryConfig(enabled=False, connection_string="x")
        assert cfg.is_active is False

    def test_enabled_without_connection_string_is_inactive(self):
        cfg = TelemetryConfig(enabled=True, connection_string="")
        assert cfg.is_active is False

    def test_enabled_with_whitespace_only_string_is_inactive(self):
        cfg = TelemetryConfig(enabled=True, connection_string="   ")
        assert cfg.is_active is False

    def test_both_set_is_active(self):
        cfg = TelemetryConfig(enabled=True, connection_string="InstrumentationKey=abc;IngestionEndpoint=https://x")
        assert cfg.is_active is True

    def test_sample_rate_clamped(self):
        # Pydantic validation enforces 0..1
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TelemetryConfig(sample_rate=1.5)
        with pytest.raises(ValidationError):
            TelemetryConfig(sample_rate=-0.1)


class TestTelemetrySinkUnavailableSDK:
    """When the Azure SDK isn't installed, emit() must silently no-op."""

    def test_emit_when_sdk_missing(self, monkeypatch):
        # Simulate SDK absence by forcing the import to raise.
        import builtins
        real_import = builtins.__import__

        def bomb(name, *args, **kwargs):
            if name.startswith("azure.monitor.opentelemetry"):
                raise ImportError("simulated missing dep")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", bomb)

        sink = TelemetrySink(connection_string="InstrumentationKey=fake;")
        # Should NOT raise — sink marks itself unavailable and drops the event
        sink.emit(*events.startup(profile="x"))
        assert sink._unavailable is True

        # Subsequent emits also no-op without re-trying the import
        sink.emit(*events.query(endpoint="vendors", has_filter=False))
