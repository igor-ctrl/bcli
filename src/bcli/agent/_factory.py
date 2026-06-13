"""Backend dispatch for :func:`bcli.agent.get_agent_backend`.

Mirror of :mod:`bcli.ask._factory` — same built-in shortcuts, same
``module.path:ClassName`` import spec for third-party, same
NullAgentBackend fallback on any failure plus one-shot warning.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING

from bcli.agent._protocol import AgentSessionBackend, NullAgentBackend

if TYPE_CHECKING:
    from bcli.config._model import AgentConfig

logger = logging.getLogger("bcli.agent")


_BUILTIN_BACKENDS: dict[str, str] = {
    "null": "bcli.agent._protocol:NullAgentBackend",
    "pydantic-ai": "bcli.agent.backends._pydantic_ai:PydanticAIBackend",
    "claude-code": "bcli.agent.backends._claude_sdk:ClaudeCodeBackend",
    "codex": "bcli.agent.backends._codex:CodexBackend",
    # Any other backend (pi.dev shim, internal gateway, self-hosted, …)
    # is selected by full import path:
    # [agent] backend = "my_pkg.module:MyBackend"
}


def get_agent_backend(config: "AgentConfig | None") -> AgentSessionBackend:
    """Build an agent session backend from an :class:`AgentConfig`.

    Returns :class:`NullAgentBackend` when no backend is configured or
    the chosen backend can't be loaded — callers can start a session
    unconditionally and check ``is_active``.
    """
    if config is None:
        return NullAgentBackend()

    raw = (config.backend or "null").strip()
    if not raw or raw.lower() == "null":
        return NullAgentBackend()

    spec = _BUILTIN_BACKENDS.get(raw, raw)

    try:
        backend_cls = _load_class(spec)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Agent backend '%s' could not be loaded (%s); falling back "
            "to NullAgentBackend. Set [agent] backend to one of %s, or "
            "to a 'module.path:ClassName' import spec.",
            raw, e, sorted(_BUILTIN_BACKENDS.keys()),
        )
        return NullAgentBackend()

    if not hasattr(backend_cls, "from_config"):
        logger.warning(
            "Agent backend '%s' has no from_config classmethod; "
            "falling back to NullAgentBackend.", raw,
        )
        return NullAgentBackend()

    try:
        return backend_cls.from_config(config)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Agent backend '%s' from_config raised %s; falling back "
            "to NullAgentBackend.", raw, e,
        )
        return NullAgentBackend()


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


__all__ = ["get_agent_backend"]
