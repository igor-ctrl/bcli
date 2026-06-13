"""``bcli.agent`` — the agent-mode engine (Part 4 of the roadmap).

Engine emits events, renderer consumes them: every backend (pydantic-ai
BYOK, claude-agent-sdk, codex) streams uniform :class:`AgentEvent`
records through the :class:`AgentSessionBackend` protocol, and one
renderer — the Textual REPL in ``bcli_cli.repl`` or the headless
``bcli agent run`` printer — consumes them. Write safety lives inside
the tool implementations (:mod:`bcli.agent.tools._impl`), gated by
:class:`AgentRuntime` and resolved through ``awaiting_approval`` events.

Design rules enforced by the package boundary:

* Nothing in here imports from ``bcli_cli`` or ``bcli_mcp``
  (CLI → SDK only; the MCP server stays a subprocess concern).
* Optional LLM SDKs are imported lazily inside backends; the factory
  falls back to :class:`NullAgentBackend` with a one-shot warning.
"""

from __future__ import annotations

from bcli.agent._factory import get_agent_backend
from bcli.agent._prompt import build_system_prompt
from bcli.agent._protocol import (
    AgentEvent,
    AgentSessionBackend,
    EventKind,
    NullAgentBackend,
)
from bcli.agent._runtime import AgentRuntime, WriteGateDecision
from bcli.agent.memory import load_bc_md
from bcli.agent.tools import ToolRegistry, ToolSpec

__all__ = [
    "AgentEvent",
    "AgentRuntime",
    "AgentSessionBackend",
    "EventKind",
    "NullAgentBackend",
    "ToolRegistry",
    "ToolSpec",
    "WriteGateDecision",
    "build_system_prompt",
    "get_agent_backend",
    "load_bc_md",
]
