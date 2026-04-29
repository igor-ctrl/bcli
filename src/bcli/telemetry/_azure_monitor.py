"""Azure Application Insights backend (``backend = "azure_monitor"``).

Wraps ``azure-monitor-opentelemetry`` so events show up under
``customEvents`` in Log Analytics. Selected by the
:func:`bcli.telemetry.get_sink` factory when the user sets
``[telemetry] backend = "azure_monitor"``.

The Azure SDK lives in the optional ``[telemetry]`` extra. If the import
fails (extra not installed, broken environment), this sink falls back to
a no-op state — telemetry must never crash the CLI.

The sink piggybacks on Python's logging module: the Azure exporter
attaches a handler to the ``bcli.telemetry.events`` logger and ships
records on a background thread, so :meth:`emit` is non-blocking.
"""

from __future__ import annotations

import logging
import random
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bcli.config._model import TelemetryConfig

_diag_logger = logging.getLogger("bcli.telemetry")

_event_logger = logging.getLogger("bcli.telemetry.events")
_event_logger.setLevel(logging.INFO)
_event_logger.propagate = False


class AzureMonitorSink:
    """Telemetry backend that ships events to Azure Application Insights."""

    is_active: bool = True

    def __init__(self, *, connection_string: str, sample_rate: float = 1.0) -> None:
        self._connection_string = connection_string
        self._sample_rate = max(0.0, min(1.0, sample_rate))
        self._configured = False
        self._unavailable = False
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: "TelemetryConfig") -> "AzureMonitorSink":
        return cls(
            connection_string=config.connection_string,
            sample_rate=config.sample_rate,
        )

    def _ensure_configured(self) -> None:
        if self._configured or self._unavailable:
            return
        with self._lock:
            if self._configured or self._unavailable:
                return
            if not self._connection_string.strip():
                _diag_logger.debug(
                    "azure_monitor backend selected but connection_string is empty; "
                    "treating as unavailable."
                )
                self._unavailable = True
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
                    "events will be dropped. Install bcli[telemetry] to enable.",
                    e,
                )
                self._unavailable = True
            except Exception as e:  # noqa: BLE001
                _diag_logger.debug("Azure Monitor configure failed: %s", e)
                self._unavailable = True

    def emit(self, name: str, properties: dict[str, Any]) -> None:
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

    def flush(self, timeout: float = 2.0) -> None:  # noqa: ARG002
        # OpenTelemetry's BatchLogProcessor flushes on its periodic timer
        # and again on shutdown; nothing to do here for v1.
        return None
