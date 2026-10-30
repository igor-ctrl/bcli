"""Tests for telemetry sinks — backend dispatch, fallbacks, privacy."""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from pydantic import ValidationError

from bcli.config._model import TelemetryConfig
from bcli.telemetry import (
    AzureMonitorSink,
    ConsoleSink,
    NullSink,
    TelemetrySink,
    _azure_monitor,
    events,
    get_sink,
)
from bcli.telemetry._azure_monitor import parse_connection_string


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


CS = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000001;"
    "IngestionEndpoint=https://example.in.applicationinsights.azure.com/;"
    "LiveEndpoint=https://example.livediagnostics.monitor.azure.com/"
)


class TestAzureMonitorSink:
    """Spools envelopes locally; never touches the network in-process."""

    def _spooled(self, spool_dir):
        path = spool_dir / "spool.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_connection_string_parsing(self):
        assert parse_connection_string(CS) == (
            "00000000-0000-0000-0000-000000000001",
            "https://example.in.applicationinsights.azure.com/v2.1/track",
        )
        # Key only → Azure's documented default ingestion endpoint.
        assert parse_connection_string("InstrumentationKey=k")[1] == (
            "https://dc.services.visualstudio.com/v2.1/track"
        )

    def test_emit_spools_an_app_insights_event(self, tmp_path):
        sink = AzureMonitorSink(connection_string=CS, spool_dir=tmp_path)
        sink.emit(*events.command(command="get vendors", profile="p", duration_ms=12.5))
        (env,) = self._spooled(tmp_path)
        assert env["name"] == "Microsoft.ApplicationInsights.Event"
        assert env["iKey"] == "00000000-0000-0000-0000-000000000001"
        base = env["data"]["baseData"]
        assert env["data"]["baseType"] == "EventData"
        assert base["name"] == "bcli.command"
        # App Insights properties are string-valued.
        assert base["properties"]["duration_ms"] == "12.5"
        assert all(isinstance(v, str) for v in base["properties"].values())

    def test_missing_instrumentation_key_drops_events(self, tmp_path):
        sink = AzureMonitorSink(connection_string="", spool_dir=tmp_path)
        sink.emit(*events.startup(profile="x"))
        assert self._spooled(tmp_path) == []
        sink.flush()  # nothing spooled → nothing to start

    def test_spool_cap_drops_new_events(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_azure_monitor, "MAX_SPOOL_BYTES", 1)
        sink = AzureMonitorSink(connection_string=CS, spool_dir=tmp_path)
        sink.emit(*events.startup(profile="x"))  # first write fits (0 bytes waiting)
        sink.emit(*events.startup(profile="x"))  # over the cap → dropped
        assert len(self._spooled(tmp_path)) == 1

    def test_full_spool_still_starts_the_shipper(self, tmp_path, monkeypatch):
        # A later command that only hits the cap must still drain the backlog,
        # or a full spool would never ship again.
        monkeypatch.setattr(_azure_monitor, "MAX_SPOOL_BYTES", 1)
        (tmp_path / "outbox-1.jsonl").write_text("{}\n")
        calls = []
        monkeypatch.setattr(_azure_monitor.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
        sink = AzureMonitorSink(connection_string=CS, spool_dir=tmp_path)
        sink.emit(*events.startup(profile="x"))
        assert self._spooled(tmp_path) == []  # dropped
        sink.flush()
        assert len(calls) == 1

    def test_unwritable_spool_never_raises(self, tmp_path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("")
        sink = AzureMonitorSink(connection_string=CS, spool_dir=blocker / "spool")
        sink.emit(*events.startup(profile="x"))  # must not raise

    def test_flush_starts_detached_shipper_only_after_spooling(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(_azure_monitor.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)))
        sink = AzureMonitorSink(connection_string=CS, spool_dir=tmp_path)
        sink.flush()
        assert calls == []
        sink.emit(*events.startup(profile="x"))
        sink.flush()
        (argv, kw), = calls
        assert argv[1:] == [
            "-m", "bcli.telemetry._ship", str(tmp_path),
            "https://example.in.applicationinsights.azure.com/v2.1/track",
        ]
        assert kw["stdout"] is subprocess.DEVNULL and kw["stderr"] is subprocess.DEVNULL
        if os.name != "nt":
            assert kw["start_new_session"] is True


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
