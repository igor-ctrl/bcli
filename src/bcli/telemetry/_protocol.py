"""Telemetry sink protocol + always-available NullSink and ConsoleSink.

Any class that satisfies :class:`TelemetrySink` can be plugged in as the
``[telemetry] backend`` for bcli. Built-in backends live in this package;
third-party backends are loaded by ``module.path:ClassName`` import path.

A backend MUST expose:

* ``is_active`` — class- or instance-level boolean. Drives the ``state.telemetry.is_active``
  short-circuit so callers can skip work when telemetry is disabled.
* ``emit(name, properties)`` — fire-and-forget; must not raise.
* ``flush(timeout)`` — best-effort flush at process exit; must not block longer
  than ``timeout`` seconds.
* ``from_config(cls, config)`` — classmethod returning a configured instance,
  used by :func:`bcli.telemetry.get_sink`.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from bcli.config._model import TelemetryConfig


@runtime_checkable
class TelemetrySink(Protocol):
    """Structural type for telemetry backends."""

    is_active: bool

    def emit(self, name: str, properties: dict[str, Any]) -> None: ...

    def flush(self, timeout: float = 2.0) -> None: ...


class NullSink:
    """Zero-overhead sink. Used when telemetry is disabled."""

    is_active: bool = False

    @classmethod
    def from_config(cls, config: "TelemetryConfig") -> "NullSink":  # noqa: ARG003
        return cls()

    def emit(self, name: str, properties: dict[str, Any]) -> None:  # noqa: ARG002
        return None

    def flush(self, timeout: float = 2.0) -> None:  # noqa: ARG002
        return None


class ConsoleSink:
    """Pretty-prints events to stderr — handy for local development.

    Activated with ``[telemetry] backend = "console"``. No external deps,
    no network egress; safe to leave on while iterating on event schemas.
    """

    is_active: bool = True

    def __init__(self) -> None:
        self._stream = sys.stderr

    @classmethod
    def from_config(cls, config: "TelemetryConfig") -> "ConsoleSink":  # noqa: ARG003
        return cls()

    def emit(self, name: str, properties: dict[str, Any]) -> None:
        try:
            payload = json.dumps({"event": name, **properties}, default=str)
            print(f"[bcli.telemetry] {payload}", file=self._stream)
        except Exception:  # noqa: BLE001
            # Never break the CLI over a logging path.
            return None

    def flush(self, timeout: float = 2.0) -> None:  # noqa: ARG002
        try:
            self._stream.flush()
        except Exception:  # noqa: BLE001
            return None
