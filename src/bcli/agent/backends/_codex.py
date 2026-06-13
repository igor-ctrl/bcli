"""Codex backend — the loop the harness owns, via the openai-codex SDK.

DEVIATION FROM THE PLAN: the plan assumed an ``import codex`` JSON-RPC
``thread/turn/item`` surface. The actually-published package is
``openai-codex`` (import name ``openai_codex``, beta 0.1.x) exposing a
higher-level client: ``AsyncCodex().thread_start(...) ->
thread.turn(input) -> AsyncTurnHandle.stream()`` yielding notifications,
plus a ``TurnResult`` with ``final_response`` / ``items``. This backend
targets that real API and maps its notifications onto bcli's uniform
:class:`AgentEvent` stream.

Codex is itself an MCP *client*, so it consumes bcli's existing
``bcli_mcp`` stdio server — no new tool code. :func:`to_mcp_config`
builds the ``mcp_servers`` config entry codex needs (command + args +
profile env). The write gate runs one layer down, inside the bcli
subprocess the MCP server drives (``confirm_write_or_exit`` +
``disable_writes``), reinforced by codex's own ``approval_mode``.

Requires the ``[agent-codex]`` extra. Import failure → NullAgentBackend
via the factory. Auth: ``CODEX_API_KEY`` / ``OPENAI_API_KEY`` (sanctioned)
or, with explicit consent, the ChatGPT subscription login in
``~/.codex/auth.json`` — both reused automatically by the SDK.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
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


def to_mcp_config(profile_name: str = "") -> dict[str, Any]:
    """Build the codex ``mcp_servers`` entry that registers ``bcli_mcp``.

    Codex launches the server as a stdio subprocess. Prefer the installed
    ``bcli-mcp`` console script; fall back to ``python -m bcli_mcp`` when
    it isn't on PATH (e.g. an editable checkout without the script).
    The active profile is passed through ``BCLI_PROFILE`` so the MCP
    server's tools resolve the same registry + safety constraints.
    """
    if shutil.which("bcli-mcp"):
        command, args = "bcli-mcp", []
    else:
        command, args = sys.executable, ["-m", "bcli_mcp"]
    env: dict[str, str] = {}
    if profile_name:
        env["BCLI_PROFILE"] = profile_name
    return {
        MCP_SERVER_NAME: {
            "command": command,
            "args": args,
            "env": env,
        }
    }


def _approval_mode(runtime: "AgentRuntime", sdk: Any) -> Any:
    """Map bcli's plan/production posture onto codex's ApprovalMode.

    Production or plan mode → the most cautious mode codex offers (review
    every action); otherwise codex's auto-review default. We probe the
    enum defensively so a renamed member in a beta release degrades to
    the default rather than crashing.
    """
    mode = getattr(sdk, "ApprovalMode", None)
    if mode is None:
        return None
    cautious = runtime.is_production or runtime.plan_mode
    if cautious:
        for name in ("on_request", "always", "untrusted", "auto_review"):
            member = getattr(mode, name, None)
            if member is not None:
                return member
    return getattr(mode, "auto_review", None)


class CodexBackend:
    """AgentSessionBackend over the openai-codex AsyncCodex client."""

    is_active: bool = True

    def __init__(self, *, model: str = "", max_turns: int = 20) -> None:
        self._model = model
        self._max_turns = max_turns
        self._runtime: "AgentRuntime | None" = None
        self._system_prompt = ""
        self._codex: Any = None
        self._thread: Any = None
        self.model_label = model or "codex"

    @classmethod
    def from_config(cls, config: "AgentConfig") -> "CodexBackend":
        import openai_codex  # noqa: F401 — fail fast when the extra is missing

        return cls(model=config.model, max_turns=config.max_steps)

    async def start_session(
        self,
        *,
        system_prompt: str,
        tools: "ToolRegistry",  # noqa: ARG002 — codex uses bcli_mcp, not the projection
        runtime: "AgentRuntime",
    ) -> None:
        self._system_prompt = system_prompt
        self._runtime = runtime

    async def _ensure_thread(self) -> None:
        import openai_codex

        if self._codex is None:
            self._codex = openai_codex.AsyncCodex()
            await self._codex.__aenter__()
        if self._thread is None:
            assert self._runtime is not None
            config = {"mcp_servers": to_mcp_config(self._runtime.profile_name)}
            kwargs: dict[str, Any] = {
                "base_instructions": self._system_prompt,
                "config": config,
            }
            approval = _approval_mode(self._runtime, openai_codex)
            if approval is not None:
                kwargs["approval_mode"] = approval
            if self._model:
                kwargs["model"] = self._model
            self._thread = await self._codex.thread_start(**kwargs)

    async def send(self, user_msg: str) -> AsyncIterator[AgentEvent]:
        if self._runtime is None:
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
        try:
            await self._ensure_thread()
            handle = await self._thread.turn(user_msg)
            async for notification in handle.stream():
                ev = _notification_to_event(notification)
                if ev is not None:
                    await queue.put(ev)
            result = await handle.run()
            final = getattr(result, "final_response", None) or ""
            await queue.put(AgentEvent(kind="turn_complete", text=final))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("codex turn failed", exc_info=True)
            await queue.put(AgentEvent(kind="error", error=str(exc)))
            await queue.put(AgentEvent(kind="turn_complete"))
        finally:
            await queue.put(_SENTINEL)

    async def close(self) -> None:
        self._thread = None
        if self._codex is not None:
            try:
                await self._codex.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._codex = None


def _notification_to_event(notification: Any) -> AgentEvent | None:
    """Best-effort map of a codex stream notification → an AgentEvent.

    The notification/item shape is beta and not fully pinned, so we probe
    common attribute names rather than isinstance-matching concrete types:
    assistant text → ``text_delta``; a tool/command/MCP item →
    ``tool_call_started``. Unknown notifications are dropped (the final
    answer still arrives via the TurnResult in :meth:`_run_turn`).
    """
    item = getattr(notification, "item", notification)
    item_type = (
        getattr(item, "type", None)
        or getattr(item, "item_type", None)
        or getattr(notification, "type", None)
        or ""
    )
    item_type = str(item_type).lower()

    text = getattr(item, "text", None) or getattr(item, "content", None)
    if isinstance(text, str) and text and (
        "message" in item_type or "assistant" in item_type or "text" in item_type
    ):
        return AgentEvent(kind="text_delta", text=text)

    if any(k in item_type for k in ("tool", "command", "mcp", "exec", "function")):
        name = (
            getattr(item, "name", None)
            or getattr(item, "tool_name", None)
            or getattr(item, "command", None)
            or item_type
        )
        args = getattr(item, "arguments", None) or getattr(item, "input", None) or {}
        if not isinstance(args, dict):
            args = {"raw": args}
        return AgentEvent(
            kind="tool_call_started",
            tool_name=_strip_mcp_prefix(str(name)),
            tool_call_id=str(getattr(item, "id", "")),
            tool_args=args,
        )
    return None


def _strip_mcp_prefix(name: str) -> str:
    prefix = f"mcp__{MCP_SERVER_NAME}__"
    return name[len(prefix):] if name.startswith(prefix) else name


__all__ = ["CodexBackend", "MCP_SERVER_NAME", "to_mcp_config"]
