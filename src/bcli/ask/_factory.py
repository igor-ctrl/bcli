"""Backend dispatch for :func:`bcli.ask.get_asker`.

Mirror of :mod:`bcli.extract._factory` — same built-in shortcuts,
same ``module.path:ClassName`` import spec for third-party, same
NullAsker fallback on any failure plus one-shot warning.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING

from bcli.ask._protocol import AskBackend, NullAsker

if TYPE_CHECKING:
    from bcli.config._model import AskConfig

logger = logging.getLogger("bcli.ask")


_BUILTIN_BACKENDS: dict[str, str] = {
    "null": "bcli.ask._protocol:NullAsker",
    "claude": "bcli.ask._claude:ClaudeAsker",
    "openai": "bcli.ask._openai:OpenAIAsker",
    # Any other backend (Cohere, internal HTTP, self-hosted, …) is
    # selected by full import path:
    # [ask] backend = "my_pkg.module:MyAsker"
}


def get_asker(config: "AskConfig | None") -> AskBackend:
    """Build an asker from an :class:`AskConfig`.

    Returns :class:`NullAsker` when no backend is configured or the
    chosen backend can't be loaded — callers can call ``ask()``
    unconditionally and check ``answer.warnings`` / ``is_active``.
    """
    if config is None:
        return NullAsker()

    raw = (config.backend or "null").strip()
    if not raw or raw.lower() == "null":
        return NullAsker()

    spec = _BUILTIN_BACKENDS.get(raw, raw)

    try:
        backend_cls = _load_class(spec)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Ask backend '%s' could not be loaded (%s); falling back "
            "to NullAsker. Set [ask] backend to one of %s, or to a "
            "'module.path:ClassName' import spec.",
            raw, e, sorted(_BUILTIN_BACKENDS.keys()),
        )
        return NullAsker()

    if not hasattr(backend_cls, "from_config"):
        logger.warning(
            "Ask backend '%s' has no from_config classmethod; "
            "falling back to NullAsker.", raw,
        )
        return NullAsker()

    try:
        return backend_cls.from_config(config)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Ask backend '%s' from_config raised %s; falling back "
            "to NullAsker.", raw, e,
        )
        return NullAsker()


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


__all__ = ["get_asker"]
