"""Backend dispatch for :func:`bcli.extract.get_extractor`.

Mirrors the telemetry factory:

* Built-in shortcut (``null``, ``claude``) → mapped to a class in this
  package.
* Anything else is treated as a ``"module.path:ClassName"`` import spec
  for a custom backend.

Failure at any step (unknown backend, import error, broken
``from_config``) falls back to :class:`NullExtractor` and logs a
one-shot warning. Extraction never crashes the CLI on config errors —
the caller sees an empty result with a warning instead.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING

from bcli.extract._protocol import ExtractorBackend, NullExtractor

if TYPE_CHECKING:
    from bcli.config._model import ExtractConfig

logger = logging.getLogger("bcli.extract")


_BUILTIN_BACKENDS: dict[str, str] = {
    "null": "bcli.extract._protocol:NullExtractor",
    "claude": "bcli.extract._claude:ClaudeExtractor",
    "openai": "bcli.extract._openai:OpenAIExtractor",
    # AWS Textract, Firecrawl, OpenDataLoader, etc. arrive as third-party
    # packages and are selected by full import path:
    # [extract] backend = "my_pkg.module:MyExtractor"
}


def get_extractor(config: "ExtractConfig | None") -> ExtractorBackend:
    """Build an extractor from an :class:`ExtractConfig`.

    Returns :class:`NullExtractor` when no backend is configured or the
    chosen backend can't be loaded — callers can call ``extract()``
    unconditionally and check ``result.warnings`` / ``is_active``.
    """
    if config is None:
        return NullExtractor()

    raw = (config.backend or "null").strip()
    if not raw or raw.lower() == "null":
        return NullExtractor()

    spec = _BUILTIN_BACKENDS.get(raw, raw)

    try:
        backend_cls = _load_class(spec)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Extract backend '%s' could not be loaded (%s); falling back "
            "to NullExtractor. Set [extract] backend to one of %s, or to "
            "a 'module.path:ClassName' import spec.",
            raw, e, sorted(_BUILTIN_BACKENDS.keys()),
        )
        return NullExtractor()

    if not hasattr(backend_cls, "from_config"):
        logger.warning(
            "Extract backend '%s' has no from_config classmethod; "
            "falling back to NullExtractor.", raw,
        )
        return NullExtractor()

    try:
        return backend_cls.from_config(config)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Extract backend '%s' from_config raised %s; falling back "
            "to NullExtractor.", raw, e,
        )
        return NullExtractor()


def _load_class(spec: str):
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
            f"Backend class '{class_name}' not found in module "
            f"'{module_path}'."
        ) from e


__all__ = ["get_extractor"]
