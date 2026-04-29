"""Azure Application Insights sink — best-effort, never blocks the CLI.

The active sink piggybacks on Python's ``logging`` module: the Azure
Monitor exporter installed by ``configure_azure_monitor()`` automatically
ships records on a background thread, so :meth:`TelemetrySink.emit` is
non-blocking in the steady state.

Three properties we explicitly guarantee:

* **No-op when telemetry is disabled.** Construction is free; emit is
  free. Callers don't need an ``if`` gate.
* **Soft fail when the SDK isn't installed.** The
  ``azure-monitor-opentelemetry`` package lives in the optional
  ``[telemetry]`` extra. If the import fails we mark the sink
  unavailable, log once at DEBUG, and keep running.
* **Sample-rate gate.** ``sample_rate`` (0..1) is applied per-event
  before the SDK is even touched. ``1.0`` (default) sends everything.
"""

from __future__ import annotations

import logging
import random
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bcli.config._model import TelemetryConfig

# Internal logger for sink diagnostics — separate from the event logger.
_diag_logger = logging.getLogger("bcli.telemetry")

# The event logger. When the Azure exporter is configured, its handler is
# attached here and ships records to App Insights as customEvents.
_event_logger = logging.getLogger("bcli.telemetry.events")
_event_logger.setLevel(logging.INFO)
_event_logger.propagate = False


class NullSink:
    """Sink for the disabled-telemetry case. Every method is a no-op."""

    is_active: bool = False

    def emit(self, name: str, properties: dict[str, Any]) -> None:  # noqa: D401, ARG002
        return None

    def flush(self, timeout: float = 2.0) -> None:  # noqa: ARG002
        return None


class TelemetrySink:
    """Wraps the Azure SDK behind a tiny ``emit()`` surface.

    Construction does *not* import the Azure SDK — the import is deferred
    to first emit so users without the optional dependency never hit
    ``ImportError``.
    """

    is_active: bool = True

    def __init__(self, *, connection_string: str, sample_rate: float = 1.0) -> None:
        self._connection_string = connection_string
        self._sample_rate = max(0.0, min(1.0, sample_rate))
        self._configured = False
        self._unavailable = False
        self._lock = threading.Lock()

    def _ensure_configured(self) -> None:
        if self._configured or self._unavailable:
            return
        with self._lock:
            if self._configured or self._unavailable:
                return
            try:
                from azure.monitor.opentelemetry import configure_azure_monitor

                configure_azure_monitor(
                    connection_string=self._connection_string,
                    logger_name="bcli.telemetry.events",
                )
                self._configured = True
                _diag_logger.debug("Azure Monitor configured for bcli telemetry.")
            except ImportError as e:
                _diag_logger.debug(
                    "azure-monitor-opentelemetry not installed (%s); "
                    "telemetry events will be dropped. "
                    "Install the [telemetry] extra to enable shipping.",
                    e,
                )
                self._unavailable = True
            except Exception as e:  # noqa: BLE001
                # Anything else (bad connection string, network at config-time, etc.)
                # — fail soft. Telemetry must never crash the CLI.
                _diag_logger.debug("Azure Monitor configure failed: %s", e)
                self._unavailable = True

    def emit(self, name: str, properties: dict[str, Any]) -> None:
        """Ship one event. Returns immediately; SDK uploads in background."""
        if self._sample_rate < 1.0 and random.random() > self._sample_rate:
            return
        self._ensure_configured()
        if self._unavailable:
            return
        try:
            _event_logger.info(
                name,
                extra={"custom_dimensions": {**properties, "event_name": name}},
            )
        except Exception:  # noqa: BLE001
            _diag_logger.debug("Failed to enqueue telemetry event %s", name, exc_info=True)

    def flush(self, timeout: float = 2.0) -> None:
        """Best-effort flush of pending events at shutdown.

        OpenTelemetry's BatchLogProcessor flushes on its periodic timer
        and again on shutdown; we don't reach into it here. Provided as
        a hook for future explicit-flush behaviour without changing
        callers.
        """
        return None


def get_sink(config: "TelemetryConfig | None") -> NullSink | TelemetrySink:
    """Factory: returns a real sink iff config opts in *and* has a connection string."""
    if config is None or not config.is_active:
        return NullSink()
    return TelemetrySink(
        connection_string=config.connection_string,
        sample_rate=config.sample_rate,
    )
