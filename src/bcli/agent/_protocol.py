"""AgentSessionBackend protocol + always-available NullAgentBackend.

Mirror of :mod:`bcli.ask._protocol` so the factory dispatch
(:mod:`bcli.agent._factory`) can stay byte-identical in shape with the
ask/extract factories.

The seam is the *session*, not the model call: pydantic-ai is a loop
bcli owns, while claude-agent-sdk and codex are loops the harness owns.
Every backend therefore reduces to the same contract — start a session
with a system prompt + tool registry + runtime, then ``send()`` user
messages and stream uniform :class:`AgentEvent` records back. One
renderer (the Textual REPL or the headless ``bcli agent run`` printer)
consumes events from any backend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from bcli.agent._runtime import AgentRuntime
    from bcli.agent.tools._registry import ToolRegistry
    from bcli.config._model import AgentConfig


EventKind = Literal[
    "text_delta",
    "tool_call_started",
    "tool_result",
    "awaiting_approval",
    "turn_complete",
    "error",
]


@dataclass(frozen=True)
class AgentEvent:
    """One uniform event in an agent turn.

    ``kind`` drives which payload fields are meaningful:

    * ``text_delta``        — ``text`` carries an incremental chunk of
                              assistant output.
    * ``tool_call_started`` — ``tool_name`` / ``tool_call_id`` /
                              ``tool_args`` describe the call about to run.
    * ``tool_result``       — ``tool_name`` / ``tool_call_id`` plus
                              ``result`` (JSON-able payload the model saw).
    * ``awaiting_approval`` — a gated write is paused on a human decision;
                              ``approval_id`` is resolved via
                              :meth:`bcli.agent.AgentRuntime.resolve_approval`,
                              ``reason`` explains why the gate fired.
    * ``turn_complete``     — the turn finished; ``text`` carries the
                              final assembled answer when available.
    * ``error``             — ``error`` carries a human-readable message.
    """

    kind: EventKind
    text: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    tool_args: Mapping[str, Any] = field(default_factory=dict)
    result: Any = None
    approval_id: str = ""
    reason: str = ""
    error: str = ""


@runtime_checkable
class AgentSessionBackend(Protocol):
    """Structural type every agent backend satisfies."""

    is_active: bool

    async def start_session(
        self,
        *,
        system_prompt: str,
        tools: "ToolRegistry",
        runtime: "AgentRuntime",
    ) -> None: ...

    def send(self, user_msg: str) -> AsyncIterator[AgentEvent]: ...

    async def close(self) -> None: ...


class NullAgentBackend:
    """Zero-overhead backend used when no backend is configured.

    The REPL / ``bcli agent run`` surface this with a "set [agent]
    backend = 'pydantic-ai' …" message so the user knows why nothing
    happened.
    """

    is_active: bool = False

    SETUP_HINT = (
        "No agent backend configured. Run 'bcli agent init' to set one "
        "up, or set [agent] backend = 'pydantic-ai' in "
        "~/.config/bcli/config.toml and install bc-cli[agent-local]."
    )

    @classmethod
    def from_config(cls, config: "AgentConfig") -> "NullAgentBackend":  # noqa: ARG003
        return cls()

    async def start_session(  # noqa: ARG002
        self,
        *,
        system_prompt: str,
        tools: "ToolRegistry",
        runtime: "AgentRuntime",
    ) -> None:
        return None

    async def send(self, user_msg: str) -> AsyncIterator[AgentEvent]:  # noqa: ARG002
        yield AgentEvent(kind="error", error=self.SETUP_HINT)
        yield AgentEvent(kind="turn_complete")

    async def close(self) -> None:
        return None


__all__ = [
    "AgentEvent",
    "AgentSessionBackend",
    "EventKind",
    "NullAgentBackend",
]
