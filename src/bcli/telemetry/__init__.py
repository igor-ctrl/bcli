"""Optional usage-telemetry sink for bcli.

The whole package no-ops when ``[telemetry] enabled = false`` (the default),
so any caller can unconditionally request a sink without first checking
the config. Active sinks ship structured events to Azure Application
Insights via OpenTelemetry; privacy-sensitive fields are dropped unless
the user explicitly opts in.

Public surface
==============

>>> from bcli.telemetry import get_sink, events
>>> sink = get_sink(config.telemetry)
>>> sink.emit(*events.query(endpoint="vendors", has_filter=True, status=200, latency_ms=312.4))
"""

from __future__ import annotations

from bcli.telemetry import events
from bcli.telemetry._appinsights import NullSink, TelemetrySink, get_sink

__all__ = ["NullSink", "TelemetrySink", "events", "get_sink"]
