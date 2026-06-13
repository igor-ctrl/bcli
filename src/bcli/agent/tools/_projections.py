"""Projections: one tool surface, three runtimes.

* :func:`to_pydantic_ai` — ``pydantic_ai.tools.Tool`` objects built from
  the describe-derived JSON schemas (``Tool.from_schema``), each wrapping
  the same in-process handler from :mod:`bcli.agent.tools._impl`.
* :func:`to_claude_sdk_tools` — ``claude_agent_sdk.tool``-decorated
  handlers for ``create_sdk_mcp_server`` (used by the claude-code
  backend; same ``_impl`` handlers, same gate).
* The codex backend needs no projection: codex is an MCP client and
  consumes the existing ``bcli_mcp`` server (see
  ``bcli.agent.backends._codex.to_mcp_config``).

All imports of optional SDKs are local to the projection functions so
this module imports cleanly with no extras installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bcli.agent.tools._impl import get_handler

if TYPE_CHECKING:
    from bcli.agent._runtime import AgentRuntime
    from bcli.agent.tools._registry import ToolRegistry, ToolSpec

logger = logging.getLogger("bcli.agent")


def _wrap_handler(spec: "ToolSpec", runtime: "AgentRuntime"):
    """Bind one handler to the runtime; never raise into the model loop."""
    handler = get_handler(spec.path)

    async def call(**kwargs: Any) -> Any:
        try:
            return await handler(runtime, **kwargs)
        except Exception as exc:  # noqa: BLE001 — surfaced to the model
            logger.debug("tool %s failed", spec.name, exc_info=True)
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}

    call.__name__ = spec.name
    call.__doc__ = spec.description
    return call


def to_pydantic_ai(
    registry: "ToolRegistry",
    runtime: "AgentRuntime",
    *,
    plan_mode: bool = False,
) -> list[Any]:
    """Project the active tool surface as pydantic-ai ``Tool`` objects."""
    from pydantic_ai.tools import Tool

    tools: list[Any] = []
    for spec in registry.specs(plan_mode=plan_mode):
        tools.append(Tool.from_schema(
            _wrap_handler(spec, runtime),
            name=spec.name,
            description=spec.description,
            json_schema=spec.input_schema,
        ))
    return tools


def to_claude_sdk_tools(
    registry: "ToolRegistry",
    runtime: "AgentRuntime",
    *,
    plan_mode: bool = False,
) -> list[Any]:
    """Project the active tool surface as claude-agent-sdk tools.

    Returns ``SdkMcpTool`` definitions ready for
    ``create_sdk_mcp_server(name="bcli", tools=...)``. The SDK expects
    handlers that take a single ``args`` dict and return an MCP-shaped
    ``{"content": [{"type": "text", "text": …}]}`` payload.
    """
    import json as _json

    from claude_agent_sdk import tool as sdk_tool

    tools: list[Any] = []
    for spec in registry.specs(plan_mode=plan_mode):
        wrapped = _wrap_handler(spec, runtime)

        def make(inner):
            async def mcp_handler(args: dict[str, Any]) -> dict[str, Any]:
                result = await inner(**(args or {}))
                text = (
                    result if isinstance(result, str)
                    else _json.dumps(result, ensure_ascii=False, default=str)
                )
                return {"content": [{"type": "text", "text": text}]}
            return mcp_handler

        tools.append(sdk_tool(
            spec.name, spec.description, spec.input_schema,
        )(make(wrapped)))
    return tools


__all__ = ["to_claude_sdk_tools", "to_pydantic_ai"]
