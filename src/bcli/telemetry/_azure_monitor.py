"""Azure Application Insights backend (``backend = "azure_monitor"``).

Events show up as ``customEvents`` (``AppEvents`` in a workspace-based
resource). Selected by the :func:`bcli.telemetry.get_sink` factory when the
user sets ``[telemetry] backend = "azure_monitor"``.

Nothing touches the network while a command runs. :meth:`emit` appends one
Application Insights envelope per event to a local spool file (a few
microseconds), and :meth:`flush` — called once at process exit — starts a
detached ``python -m bcli.telemetry._ship`` that posts the spool to the
ingestion endpoint after the command has already returned. An offline
laptop keeps its events on disk and ships them on a later run.

Sending through ``azure-monitor-opentelemetry`` instead cost ~2 s per
command (exporter shutdown at exit) and printed an IMDS-probe traceback on
every machine that isn't an Azure VM, so the envelope is built here and
posted with ``httpx``.
"""

from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bcli.config._model import TelemetryConfig

_diag_logger = logging.getLogger("bcli.telemetry")

# Default from Azure's connection-string spec, used when the string carries
# only an InstrumentationKey.
_DEFAULT_INGESTION = "https://dc.services.visualstudio.com/"

# Stop spooling once this much is waiting to ship (an offline laptop must
# not grow the directory without bound). Events past the cap are dropped.
MAX_SPOOL_BYTES = 5 * 1024 * 1024

SPOOL_NAME = "spool.jsonl"


def parse_connection_string(connection_string: str) -> tuple[str, str]:
    """Return ``(instrumentation_key, track_url)``; key is ``""`` if absent."""
    parts = {}
    for item in connection_string.split(";"):
        key, sep, value = item.partition("=")
        if sep:
            parts[key.strip().lower()] = value.strip()
    endpoint = parts.get("ingestionendpoint") or _DEFAULT_INGESTION
    return parts.get("instrumentationkey", ""), endpoint.rstrip("/") + "/v2.1/track"


def _envelope(ikey: str, name: str, properties: dict[str, Any]) -> dict[str, Any]:
    # App Insights properties are string-valued; KQL reads numbers back
    # with todouble().
    props = {k: "" if v is None else str(v) for k, v in properties.items()}
    return {
        "name": "Microsoft.ApplicationInsights.Event",
        "time": datetime.now(timezone.utc).isoformat(),
        "iKey": ikey,
        "tags": {"ai.cloud.role": "bcli"},
        "data": {
            "baseType": "EventData",
            "baseData": {"ver": 2, "name": name, "properties": props},
        },
    }


def _spool_bytes(spool_dir: Path) -> int:
    try:
        return sum(p.stat().st_size for p in spool_dir.glob("*.jsonl"))
    except OSError:
        return 0


class AzureMonitorSink:
    """Telemetry backend that ships events to Azure Application Insights."""

    is_active: bool = True

    def __init__(
        self,
        *,
        connection_string: str,
        sample_rate: float = 1.0,
        spool_dir: Path | None = None,
    ) -> None:
        self._ikey, self._track_url = parse_connection_string(connection_string)
        self._sample_rate = max(0.0, min(1.0, sample_rate))
        if spool_dir is None:
            from bcli.config._defaults import TELEMETRY_DIR

            spool_dir = TELEMETRY_DIR
        self._spool_dir = spool_dir
        self._spooled = False
        self._unavailable = not self._ikey
        if self._unavailable:
            _diag_logger.debug(
                "azure_monitor backend selected but the connection string has no "
                "InstrumentationKey; events will be dropped."
            )

    @classmethod
    def from_config(cls, config: "TelemetryConfig") -> "AzureMonitorSink":
        return cls(
            connection_string=config.connection_string,
            sample_rate=config.sample_rate,
        )

    def emit(self, name: str, properties: dict[str, Any]) -> None:
        if self._unavailable:
            return
        if self._sample_rate < 1.0 and random.random() > self._sample_rate:
            return
        try:
            self._spool_dir.mkdir(parents=True, exist_ok=True)
            if _spool_bytes(self._spool_dir) >= MAX_SPOOL_BYTES:
                return
            line = json.dumps(_envelope(self._ikey, name, properties), default=str)
            # One write() of one line in append mode: concurrent bcli
            # processes don't interleave within a line.
            with open(self._spool_dir / SPOOL_NAME, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._spooled = True
        except Exception:  # noqa: BLE001
            _diag_logger.debug("Failed to spool telemetry event %s", name, exc_info=True)

    def flush(self, timeout: float = 2.0) -> None:  # noqa: ARG002
        """Start a detached shipper if this process spooled anything.

        Never waits for it: the command has finished, and the shipper owns
        the network round trip from here.
        """
        if not self._spooled:
            return
        try:
            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "close_fds": True,
            }
            if os.name == "nt":
                kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(  # noqa: S603 — fixed argv, our own module
                [sys.executable, "-m", "bcli.telemetry._ship", str(self._spool_dir), self._track_url],
                **kwargs,
            )
        except Exception:  # noqa: BLE001
            _diag_logger.debug("Failed to start telemetry shipper", exc_info=True)
