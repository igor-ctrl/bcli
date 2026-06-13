"""Claude Code backend — the loop the harness owns.

Drives the user's installed Claude Code through the ``claude-agent-sdk``
package (Python 0.2.x). bcli's verbs are exposed as an in-process SDK MCP
server built from the SAME ``tools/_impl.py`` handlers the pydantic-ai
backend uses (via :func:`bcli.agent.tools._projections.to_claude_sdk_tools`),
so write safety lives in one place regardless of backend.

Design notes (from the SDK docs + the documented Python quirks):

* ``ClaudeAgentOptions(system_prompt=…)`` carries bcli's prompt;
  ``allowed_tools`` is restricted to our ``mcp__bcli__*`` tools and the
  built-in coding tools are never allowed — the agent can only touch BC
  through bcli.
* The write gate already lives inside the tool handlers (they emit
  ``awaiting_approval`` and await the runtime future). The SDK's
  ``can_use_tool`` callback is a second, coarser fence: allow only the
  bcli MCP tools, deny anything else.
* **Quirk**: ``can_use_tool`` only fires in *streaming* mode (an
  ``AsyncIterable`` prompt), and even then needs a dummy ``PreToolUse``
  hook returning ``{"continue_": True}`` to keep the stream open. Both
  are wired below.

Requires the ``[agent-claude-code]`` extra. Import failure falls back to
NullAgentBackend via the factory. Auth: ``ANTHROPIC_API_KEY`` (sanctioned)
or, with explicit consent, ``CLAUDE_CODE_OAUTH_TOKEN`` /
subscription login — both picked up from the environment by the SDK.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from bcli.agent._protocol import AgentEvent

if TYPE_CHECKING:
    from bcli.agent._runtime import AgentRuntime
    from bcli.agent.tools._registry import ToolRegistry
    from bcli.config._model import AgentConfig

logger = logging.getLogger("bcli.agent")

MCP_SERVER_NAME = "bcli"
_SENTINEL: Any = object()


class ClaudeCodeBackend:
    """AgentSessionBackend over claude-agent-sdk's ClaudeSDKClient."""

    is_active: bool = True

    def __init__(self, *, model: str = "", max_turns: int = 20) -> None:
        self._model = model
        self._max_turns = max_turns
        self._runtime: "AgentRuntime | None" = None
        self._tools: "ToolRegistry | None" = None
        self._system_prompt = ""
        self.model_label = model or "claude-code"

    @classmethod
    def from_config(cls, config: "AgentConfig") -> "ClaudeCodeBackend":
        import claude_agent_sdk  # noqa: F401 — fail fast when the extra is missing

        return cls(model=config.model, max_turns=config.max_steps)

    # ── session ───────────────────────────────────────────────────────

    async def start_session(
        self,
        *,
        system_prompt: str,
        tools: "ToolRegistry",
        runtime: "AgentRuntime",
    ) -> None:
        # No long-lived client: claude-agent-sdk's ClaudeSDKClient is a
        # per-turn async context manager. Stash the inputs; build the
        # client in send().
        self._system_prompt = system_prompt
        self._tools = tools
        self._runtime = runtime

    def _build_options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, create_sdk_mcp_server

        from bcli.agent.tools._projections import to_claude_sdk_tools

        assert self._tools is not None and self._runtime is not None
        sdk_tools = to_claude_sdk_tools(
            self._tools, self._runtime, plan_mode=self._runtime.plan_mode,
        )
        server = create_sdk_mcp_server(
            name=MCP_SERVER_NAME, version="1.0.0", tools=sdk_tools,
        )
        allowed = [
            f"mcp__{MCP_SERVER_NAME}__{name}"
            for name in self._tools.tool_names(plan_mode=self._runtime.plan_mode)
        ]

        async def _dummy_pre_tool_hook(input_data, tool_use_id, context):  # noqa: ANN001, ARG001
            # Required to make can_use_tool fire in streaming mode.
            return {"continue_": True}

        options_kwargs: dict[str, Any] = {
            "system_prompt": self._system_prompt,
            "mcp_servers": {MCP_SERVER_NAME: server},
            "allowed_tools": allowed,
            "can_use_tool": self._make_can_use_tool(allowed),
            "hooks": {"PreToolUse": [HookMatcher(hooks=[_dummy_pre_tool_hook])]},
            "max_turns": self._max_turns,
        }
        if self._model:
            options_kwargs["model"] = self._model
        return ClaudeAgentOptions(**options_kwargs)

    def _make_can_use_tool(self, allowed: list[str]):
        """Coarse fence: allow only the bcli MCP tools, deny the rest.

        The fine-grained write gate (disable_writes / caution / prod /
        approval) runs *inside* the tool handler, so this callback just
        keeps the agent from reaching for any non-bcli capability.
        """
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        allowed_set = set(allowed)

        async def can_use_tool(tool_name, input_data, context):  # noqa: ANN001, ARG001
            if tool_name in allowed_set:
                return PermissionResultAllow(updated_input=input_data)
            return PermissionResultDeny(
                message=(
                    f"'{tool_name}' is not a bcli tool. This agent may only "
                    "use bcli's Business Central tools."
                )
            )

        return can_use_tool

    async def send(self, user_msg: str) -> AsyncIterator[AgentEvent]:
        if self._runtime is None or self._tools is None:
            yield AgentEvent(kind="error",
                             error="Session not started — call start_session first.")
            yield AgentEvent(kind="turn_complete")
            return

        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._runtime.bind_emitter(queue.put)
        task = asyncio.ensure_future(self._run_turn(user_msg, queue))
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item
        finally:
            self._runtime.bind_emitter(None)
            if not task.done():
                task.cancel()
            else:
                task.result()

    async def _run_turn(self, user_msg: str, queue: asyncio.Queue[Any]) -> None:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeSDKClient,
            ResultMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
        )

        async def _prompt_stream():
            # AsyncIterable prompt — required for can_use_tool to fire.
            yield {
                "type": "user",
                "message": {"role": "user", "content": user_msg},
            }

        final_text_parts: list[str] = []
        try:
            async with ClaudeSDKClient(options=self._build_options()) as client:
                await client.query(_prompt_stream())
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                final_text_parts.append(block.text)
                                await queue.put(AgentEvent(
                                    kind="text_delta", text=block.text,
                                ))
                            elif isinstance(block, ToolUseBlock):
                                await queue.put(AgentEvent(
                                    kind="tool_call_started",
                                    tool_name=_strip_mcp_prefix(block.name),
                                    tool_call_id=block.id,
                                    tool_args=dict(block.input or {}),
                                ))
                            elif isinstance(block, ToolResultBlock):
                                await queue.put(AgentEvent(
                                    kind="tool_result",
                                    tool_call_id=getattr(block, "tool_use_id", ""),
                                    result=getattr(block, "content", None),
                                ))
                    elif isinstance(message, ResultMessage):
                        text = getattr(message, "result", "") or "".join(final_text_parts)
                        await queue.put(AgentEvent(kind="turn_complete", text=text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("claude-code turn failed", exc_info=True)
            await queue.put(AgentEvent(kind="error", error=str(exc)))
            await queue.put(AgentEvent(kind="turn_complete"))
        finally:
            await queue.put(_SENTINEL)

    async def close(self) -> None:
        self._runtime = None
        self._tools = None


def _strip_mcp_prefix(tool_name: str) -> str:
    """``mcp__bcli__bcli_get`` → ``bcli_get`` for display parity."""
    prefix = f"mcp__{MCP_SERVER_NAME}__"
    return tool_name[len(prefix):] if tool_name.startswith(prefix) else tool_name


__all__ = ["ClaudeCodeBackend", "MCP_SERVER_NAME"]
