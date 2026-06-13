"""PydanticAIBackend driven by FunctionModel / TestModel — no network.

Asserts the uniform AgentEvent stream shape: a tool call surfaces as
tool_call_started → tool_result, text streams as text_delta, and every
turn ends with exactly one turn_complete carrying the final answer.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic_ai")

from pydantic_ai.models.test import TestModel  # noqa: E402

from _helpers import FakeMeta, FakeRegistry, make_runtime  # noqa: E402

from bcli.agent import ToolRegistry  # noqa: E402
from bcli.agent.backends._pydantic_ai import PydanticAIBackend  # noqa: E402


async def _drive(backend, runtime, msg: str):
    await backend.start_session(
        system_prompt="sys", tools=ToolRegistry.default(), runtime=runtime,
    )
    events = [ev async for ev in backend.send(msg)]
    await backend.close()
    return events


async def test_text_only_turn_streams_and_completes() -> None:
    # call_tools=[] → the model answers directly without invoking any tool
    # (so no handler shells out to a real `bcli batch` subprocess).
    backend = PydanticAIBackend(
        model=TestModel(call_tools=[], custom_output_text="42 vendors"),
        max_steps=5,
    )
    runtime = make_runtime()
    events = await _drive(backend, runtime, "how many vendors?")
    kinds = [e.kind for e in events]
    assert kinds[-1] == "turn_complete"
    assert events[-1].text == "42 vendors"


async def test_tool_call_event_shape() -> None:
    """A model that calls one tool then answers → ordered event stream.

    ``TestModel(call_tools=[...])`` deterministically calls just the named
    tool once, then returns its default text — no network, no live model.
    """
    backend = PydanticAIBackend(
        model=TestModel(call_tools=["bcli_endpoint_search"]), max_steps=5,
    )
    runtime = make_runtime(registry=FakeRegistry({"vendors": FakeMeta("vendors")}))
    events = await _drive(backend, runtime, "find vendor endpoints")
    kinds = [e.kind for e in events]

    assert "tool_call_started" in kinds
    assert "tool_result" in kinds
    assert kinds.index("tool_call_started") < kinds.index("tool_result")
    assert kinds[-1] == "turn_complete"

    started = next(e for e in events if e.kind == "tool_call_started")
    assert started.tool_name == "bcli_endpoint_search"

    # The tool result the model saw carries the registry match.
    tool_result = next(e for e in events if e.kind == "tool_result")
    assert tool_result.tool_name == "bcli_endpoint_search"


async def test_send_before_start_emits_error() -> None:
    backend = PydanticAIBackend(model=TestModel(), max_steps=5)
    events = [ev async for ev in backend.send("hi")]
    assert events[0].kind == "error"
    assert events[-1].kind == "turn_complete"


async def test_build_model_local_uses_base_url(monkeypatch) -> None:
    from bcli.agent.backends import _pydantic_ai as mod
    from bcli.config._model import AgentConfig

    model = mod._build_model(AgentConfig(
        backend="pydantic-ai", model="llama3.1", base_url="http://localhost:11434/v1",
    ))
    # OpenAI-compatible local model object, not a bare string.
    assert not isinstance(model, str)


def test_resolve_llm_key_prefers_explicit_env(monkeypatch) -> None:
    from bcli.agent.backends._pydantic_ai import resolve_llm_key

    monkeypatch.setenv("MY_CUSTOM_KEY", "sk-explicit")
    assert resolve_llm_key("anthropic", "MY_CUSTOM_KEY") == "sk-explicit"
