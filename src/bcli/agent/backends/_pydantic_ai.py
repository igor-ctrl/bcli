"""BYOK backend — pydantic-ai in-process agent loop.

This is the loop *bcli owns*: any Anthropic / OpenAI / local
OpenAI-compatible model (Ollama, vLLM, LM Studio, …) via pydantic-ai's
``provider:model`` strings and ``base_url`` override. Tools are the
in-process handlers from :mod:`bcli.agent.tools._impl` projected with
``Tool.from_schema`` — the describe-derived JSON schemas go to the model
verbatim.

Key resolution mirrors :mod:`bcli.auth._credentials`: explicit env var
(``api_key_env``) → OS keychain under the existing ``bcli`` service with
the ``llm:<provider>`` namespace → the provider's default env var.

Requires the ``[agent-local]`` extra (``pydantic-ai-slim``, pinned
``<2``). Import failures fall back to NullAgentBackend via the factory.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from bcli.agent._protocol import AgentEvent

if TYPE_CHECKING:
    from bcli.agent._runtime import AgentRuntime
    from bcli.agent.tools._registry import ToolRegistry
    from bcli.config._model import AgentConfig

logger = logging.getLogger("bcli.agent")

DEFAULT_MODEL = "anthropic:claude-sonnet-4-5"

_DEFAULT_KEY_ENVS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_SENTINEL: Any = object()


def resolve_llm_key(provider: str, api_key_env: str = "") -> str | None:
    """direct env (``api_key_env``) → keyring ``llm:<provider>`` → default env.

    Mirrors the secret-resolution order in ``bcli.auth._credentials``.
    """
    if api_key_env:
        value = os.environ.get(api_key_env)
        if value:
            return value
    try:
        import keyring

        from bcli.auth._credentials import KEYRING_SERVICE

        value = keyring.get_password(KEYRING_SERVICE, f"llm:{provider}")
        if value:
            return value
    except Exception:  # noqa: BLE001 — keyring is best-effort
        pass
    default_env = _DEFAULT_KEY_ENVS.get(provider)
    if default_env:
        return os.environ.get(default_env)
    return None


def store_llm_key(provider: str, key: str) -> bool:
    """Persist an LLM API key in the OS keychain. Returns True on success."""
    try:
        import keyring

        from bcli.auth._credentials import KEYRING_SERVICE

        keyring.set_password(KEYRING_SERVICE, f"llm:{provider}", key)
        return True
    except Exception:  # noqa: BLE001
        return False


def _build_model(config: "AgentConfig") -> Any:
    """Turn ``[agent] model / base_url / api_key_env`` into a model object."""
    raw = (config.model or DEFAULT_MODEL).strip()
    provider, _, model_name = raw.partition(":")
    if not model_name:
        provider, model_name = "openai", raw  # bare name → OpenAI-compatible

    key = resolve_llm_key(provider, config.api_key_env)

    if config.base_url or provider == "ollama":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        base_url = config.base_url or "http://localhost:11434/v1"
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=base_url, api_key=key or "local"),
        )

    if provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        if not key:
            raise ValueError(
                "No Anthropic API key found. Set ANTHROPIC_API_KEY, run "
                "'bcli agent init', or set [agent] api_key_env."
            )
        return AnthropicModel(model_name, provider=AnthropicProvider(api_key=key))

    if provider == "openai":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        if not key:
            raise ValueError(
                "No OpenAI API key found. Set OPENAI_API_KEY, run "
                "'bcli agent init', or set [agent] api_key_env."
            )
        return OpenAIChatModel(model_name, provider=OpenAIProvider(api_key=key))

    # Other providers (groq:…, mistral:…) — let pydantic-ai resolve from
    # its own env-var conventions.
    return raw


class PydanticAIBackend:
    """AgentSessionBackend over a pydantic-ai ``Agent``."""

    is_active: bool = True

    def __init__(self, *, model: Any, max_steps: int = 20) -> None:
        self._model = model
        self._max_steps = max_steps
        self._agent: Any = None
        self._runtime: "AgentRuntime | None" = None
        self._history: list[Any] | None = None
        self.model_label = getattr(model, "model_name", None) or str(model)

    @classmethod
    def from_config(cls, config: "AgentConfig") -> "PydanticAIBackend":
        import pydantic_ai  # noqa: F401 — fail fast when extra missing

        return cls(model=_build_model(config), max_steps=config.max_steps)

    # ── session ───────────────────────────────────────────────────────

    async def start_session(
        self,
        *,
        system_prompt: str,
        tools: "ToolRegistry",
        runtime: "AgentRuntime",
    ) -> None:
        from pydantic_ai import Agent

        from bcli.agent.tools._projections import to_pydantic_ai

        self._runtime = runtime
        self._agent = Agent(
            self._model,
            instructions=system_prompt,
            tools=to_pydantic_ai(tools, runtime, plan_mode=runtime.plan_mode),
        )
        self._history = None

    async def send(self, user_msg: str) -> AsyncIterator[AgentEvent]:
        if self._agent is None or self._runtime is None:
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
                task.result()  # surface unexpected crashes in tests

    async def _run_turn(self, user_msg: str, queue: asyncio.Queue[Any]) -> None:
        from pydantic_ai import AgentRunResultEvent
        from pydantic_ai.messages import (
            FunctionToolCallEvent,
            FunctionToolResultEvent,
            PartDeltaEvent,
            PartStartEvent,
        )
        from pydantic_ai.usage import UsageLimits

        final_text = ""
        try:
            async with self._agent.run_stream_events(
                user_msg,
                message_history=self._history,
                usage_limits=UsageLimits(request_limit=self._max_steps),
            ) as stream:
                async for ev in stream:
                    if isinstance(ev, PartStartEvent):
                        content = getattr(ev.part, "content", None)
                        if isinstance(content, str) and content:
                            await queue.put(AgentEvent(kind="text_delta", text=content))
                    elif isinstance(ev, PartDeltaEvent):
                        delta = getattr(ev.delta, "content_delta", None)
                        if isinstance(delta, str) and delta:
                            await queue.put(AgentEvent(kind="text_delta", text=delta))
                    elif isinstance(ev, FunctionToolCallEvent):
                        await queue.put(AgentEvent(
                            kind="tool_call_started",
                            tool_name=ev.part.tool_name,
                            tool_call_id=ev.part.tool_call_id,
                            tool_args=_args_as_dict(ev.part),
                        ))
                    elif isinstance(ev, FunctionToolResultEvent):
                        await queue.put(AgentEvent(
                            kind="tool_result",
                            tool_name=getattr(ev.part, "tool_name", ""),
                            tool_call_id=getattr(ev.part, "tool_call_id", ""),
                            result=getattr(ev.part, "content", None),
                        ))
                    elif isinstance(ev, AgentRunResultEvent):
                        self._history = ev.result.all_messages()
                        output = ev.result.output
                        final_text = output if isinstance(output, str) else str(output)
            await queue.put(AgentEvent(kind="turn_complete", text=final_text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced as an event
            logger.debug("pydantic-ai turn failed", exc_info=True)
            await queue.put(AgentEvent(kind="error", error=str(exc)))
            await queue.put(AgentEvent(kind="turn_complete"))
        finally:
            await queue.put(_SENTINEL)

    async def close(self) -> None:
        self._agent = None
        self._history = None


def _args_as_dict(part: Any) -> dict[str, Any]:
    try:
        return dict(part.args_as_dict())
    except Exception:  # noqa: BLE001
        args = getattr(part, "args", None)
        return args if isinstance(args, dict) else {"raw": args}


__all__ = ["DEFAULT_MODEL", "PydanticAIBackend", "resolve_llm_key", "store_llm_key"]
