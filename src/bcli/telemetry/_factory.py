"""Backend dispatch for :func:`bcli.telemetry.get_sink`.

The user picks a backend in their ``[telemetry] backend = "..."`` config:

* Built-in shortcut (``null``, ``console``, ``azure_monitor``) → mapped
  to a class in this package.
* Anything else is treated as a ``"module.path:ClassName"`` import spec
  for a custom sink. The class is loaded lazily and must implement
  :class:`bcli.telemetry.TelemetrySink` (i.e. expose
  ``is_active``, ``emit``, ``flush``, and a ``from_config`` classmethod).

Failure at any step (unknown backend, import error, broken
``from_config``) falls back to :class:`NullSink` and logs a one-shot
warning. Telemetry never crashes the CLI.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING

from bcli.telemetry._protocol import ConsoleSink, NullSink, TelemetrySink

if TYPE_CHECKING:
    from bcli.config._model import TelemetryConfig

logger = logging.getLogger("bcli.telemetry")


# Built-in backend aliases. Key = `[telemetry] backend` value; Value =
# "module.path:ClassName" import spec.
_BUILTIN_BACKENDS: dict[str, str] = {
    "null": "bcli.telemetry._protocol:NullSink",
    "console": "bcli.telemetry._protocol:ConsoleSink",
    "azure_monitor": "bcli.telemetry._azure_monitor:AzureMonitorSink",
    # OTLP, CloudWatch, Honeycomb, Datadog, etc. should arrive as
    # third-party packages or local modules and be selected by full
    # import path: `[telemetry] backend = "my_pkg.my_module:MySink"`.
}


def get_sink(config: "TelemetryConfig | None") -> TelemetrySink:
    """Build a sink from a :class:`TelemetryConfig`.

    Returns :class:`NullSink` when telemetry is disabled or the chosen
    backend cannot be loaded — callers can safely call ``emit()``
    without checking.
    """
    if config is None or not config.enabled:
        return NullSink()

    raw = (config.backend or "null").strip()
    if not raw or raw.lower() == "null":
        return NullSink()

    spec = _BUILTIN_BACKENDS.get(raw, raw)

    try:
        sink_cls = _load_class(spec)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Telemetry backend '%s' could not be loaded (%s); "
            "falling back to NullSink. Set [telemetry] backend to one of "
            "%s, or to a 'module.path:ClassName' import spec.",
            raw, e, sorted(_BUILTIN_BACKENDS.keys()),
        )
        return NullSink()

    if not hasattr(sink_cls, "from_config"):
        logger.warning(
            "Telemetry backend '%s' has no from_config classmethod; "
            "falling back to NullSink.",
            raw,
        )
        return NullSink()

    try:
        sink = sink_cls.from_config(config)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Telemetry backend '%s' from_config raised %s; falling back to NullSink.",
            raw, e,
        )
        return NullSink()

    return sink


def _load_class(spec: str):
    """Resolve ``"module.path:ClassName"`` to the actual class object."""
    if ":" not in spec:
        raise ValueError(
            f"Backend spec '{spec}' is missing a class — expected "
            f"'module.path:ClassName'."
        )
    module_path, _, class_name = spec.partition(":")
    if not module_path or not class_name:
        raise ValueError(
            f"Backend spec '{spec}' is malformed — expected "
            f"'module.path:ClassName'."
        )
    module = import_module(module_path)
    try:
        return getattr(module, class_name)
    except AttributeError as e:
        raise ValueError(
            f"Backend class '{class_name}' not found in module '{module_path}'."
        ) from e


__all__ = ["ConsoleSink", "NullSink", "get_sink"]
