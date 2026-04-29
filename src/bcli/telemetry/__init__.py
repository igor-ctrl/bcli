"""Optional usage-telemetry sink for bcli — plug-and-play backends.

The whole package no-ops when ``[telemetry] enabled = false`` (the default),
so callers can request a sink unconditionally without checking the config
first. Built-in backends:

* ``"null"`` (default) — drop everything
* ``"console"`` — pretty-print to stderr (dev / debugging)
* ``"azure_monitor"`` — Azure Application Insights (extra: ``[telemetry]``)

Custom backends are loaded by ``module.path:ClassName`` import spec and
must satisfy the :class:`TelemetrySink` protocol — i.e. expose
``is_active``, ``emit``, ``flush``, and a ``from_config`` classmethod.

Public surface
==============

>>> from bcli.telemetry import get_sink, events
>>> sink = get_sink(config.telemetry)                 # NullSink if disabled
>>> sink.emit(*events.query(endpoint="vendors", has_filter=True, status=200, latency_ms=312.4))
"""

from __future__ import annotations

from bcli.telemetry import events
from bcli.telemetry._azure_monitor import AzureMonitorSink
from bcli.telemetry._factory import get_sink
from bcli.telemetry._protocol import ConsoleSink, NullSink, TelemetrySink

__all__ = [
    "AzureMonitorSink",
    "ConsoleSink",
    "NullSink",
    "TelemetrySink",
    "events",
    "get_sink",
]
