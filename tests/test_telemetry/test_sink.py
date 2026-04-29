"""Tests for telemetry sinks — backend dispatch, fallbacks, privacy."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bcli.config._model import TelemetryConfig
from bcli.telemetry import (
    AzureMonitorSink,
    ConsoleSink,
    NullSink,
    TelemetrySink,
    events,
    get_sink,
)


# ── get_sink dispatch ────────────────────────────────────────────────


class TestGetSinkDispatch:
    def test_disabled_returns_null(self):
        cfg = TelemetryConfig()  # default: enabled=False
        assert isinstance(get_sink(cfg), NullSink)

    def test_none_config_returns_null(self):
        assert isinstance(get_sink(None), NullSink)

    def test_enabled_with_null_backend_returns_null(self):
        cfg = TelemetryConfig(enabled=True, backend="null")
        assert isinstance(get_sink(cfg), NullSink)

    def test_enabled_with_console_backend(self):
        cfg = TelemetryConfig(enabled=True, backend="console")
        sink = get_sink(cfg)
        assert isinstance(sink, ConsoleSink)
        assert sink.is_active is True

    def test_enabled_with_azure_monitor_backend(self):
        cfg = TelemetryConfig(
            enabled=True, backend="azure_monitor",
            connection_string="InstrumentationKey=fake;",
        )
        sink = get_sink(cfg)
        assert isinstance(sink, AzureMonitorSink)

    def test_unknown_builtin_falls_back_to_null(self, caplog):
        cfg = TelemetryConfig(enabled=True, backend="datadog")
        # 'datadog' isn't a built-in alias and isn't a 'mod:Class' spec → import fails
        sink = get_sink(cfg)
        assert isinstance(sink, NullSink)

    def test_custom_import_path_loads_class(self):
        cfg = TelemetryConfig(
            enabled=True,
            backend="bcli.telemetry._protocol:ConsoleSink",
        )
        # Custom path resolves to the same ConsoleSink class
        sink = get_sink(cfg)
        assert isinstance(sink, ConsoleSink)

    def test_custom_path_class_missing_falls_back(self):
        cfg = TelemetryConfig(
            enabled=True,
            backend="bcli.telemetry._protocol:NotARealClass",
        )
        sink = get_sink(cfg)
        assert isinstance(sink, NullSink)

    def test_custom_path_module_missing_falls_back(self):
        cfg = TelemetryConfig(
            enabled=True,
            backend="not_a_real_pkg.module:Something",
        )
        sink = get_sink(cfg)
        assert isinstance(sink, NullSink)

    def test_malformed_path_falls_back(self):
        cfg = TelemetryConfig(enabled=True, backend="no_colon_here")
        sink = get_sink(cfg)
        assert isinstance(sink, NullSink)


# ── Protocol conformance ─────────────────────────────────────────────


class TestProtocolConformance:
    def test_null_satisfies_protocol(self):
        assert isinstance(NullSink(), TelemetrySink)

    def test_console_satisfies_protocol(self):
        assert isinstance(ConsoleSink(), TelemetrySink)

    def test_azure_satisfies_protocol(self):
        sink = AzureMonitorSink(connection_string="InstrumentationKey=fake;")
        assert isinstance(sink, TelemetrySink)


# ── Built-in sinks behave well ──────────────────────────────────────


class TestNullSink:
    def test_emit_is_noop(self):
        NullSink().emit("anything", {"x": 1, "secret": "very_real"})

    def test_flush_is_noop(self):
        NullSink().flush()

    def test_from_config(self):
        cfg = TelemetryConfig()
        sink = NullSink.from_config(cfg)
        assert isinstance(sink, NullSink)


class TestConsoleSink:
    def test_emit_writes_to_stderr(self, capsys):
        sink = ConsoleSink()
        sink.emit(*events.startup(profile="dev", environment="local"))
        err = capsys.readouterr().err
        assert "[bcli.telemetry]" in err
        assert '"event": "bcli.startup"' in err

    def test_emit_handles_unserialisable_values(self):
        sink = ConsoleSink()

        class Weird:
            pass

        # Must not raise even with un-JSON-serialisable input
        sink.emit("custom", {"obj": Weird()})

    def test_from_config(self):
        sink = ConsoleSink.from_config(TelemetryConfig(enabled=True, backend="console"))
        assert isinstance(sink, ConsoleSink)


class TestAzureMonitorSink:
    """Azure SDK is the optional [telemetry] extra; sink must soft-fail without it."""

    def test_emit_when_sdk_missing(self, monkeypatch):
        # Simulate SDK absence by making its import raise
        import builtins
        real_import = builtins.__import__

        def bomb(name, *args, **kwargs):
            if name.startswith("azure.monitor.opentelemetry"):
                raise ImportError("simulated missing dep")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", bomb)

        sink = AzureMonitorSink(connection_string="InstrumentationKey=fake;")
        sink.emit(*events.startup(profile="x"))  # must not raise
        assert sink._unavailable is True

        # Subsequent emits no-op without re-trying the import
        sink.emit(*events.query(endpoint="vendors", has_filter=False))

    def test_empty_connection_string_marks_unavailable(self):
        sink = AzureMonitorSink(connection_string="")
        sink.emit(*events.startup(profile="x"))
        assert sink._unavailable is True


# ── TelemetryConfig validation ───────────────────────────────────────


class TestTelemetryConfigIsActive:
    def test_disabled_is_inactive(self):
        cfg = TelemetryConfig(enabled=False, backend="azure_monitor", connection_string="x")
        assert cfg.is_active is False

    def test_enabled_with_null_backend_is_inactive(self):
        cfg = TelemetryConfig(enabled=True, backend="null")
        assert cfg.is_active is False

    def test_enabled_with_real_backend_is_active(self):
        cfg = TelemetryConfig(enabled=True, backend="azure_monitor")
        assert cfg.is_active is True

    def test_default_is_inactive(self):
        # Pristine TelemetryConfig must be safe by default
        assert TelemetryConfig().is_active is False

    def test_sample_rate_clamped_at_validation(self):
        with pytest.raises(ValidationError):
            TelemetryConfig(sample_rate=1.5)
        with pytest.raises(ValidationError):
            TelemetryConfig(sample_rate=-0.1)
