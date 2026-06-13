"""Codex backend — mocked openai-codex (package not installed).

A fake ``openai_codex`` module is injected into ``sys.modules``. Asserts
the to_mcp_config registration of bcli_mcp, the notification → AgentEvent
mapping, approval-mode escalation under production/plan mode, and the
TurnResult → turn_complete final answer.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

from _helpers import FakeProfile, make_runtime

from bcli.agent import ToolRegistry


# ── fake openai_codex ──────────────────────────────────────────────────


class _ApprovalMode:
    auto_review = "auto_review"
    on_request = "on_request"


@dataclass
class _Item:
    type: str = ""
    text: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    id: str = ""


@dataclass
class _Notification:
    item: Any


@dataclass
class _TurnResult:
    final_response: str = ""
    items: list = field(default_factory=list)
    status: str = "completed"


class _TurnHandle:
    NOTIFICATIONS: list = []
    RESULT = _TurnResult(final_response="done")

    async def stream(self):
        for n in type(self).NOTIFICATIONS:
            yield n

    async def run(self):
        return type(self).RESULT


class _Thread:
    LAST_KWARGS: dict = {}

    async def turn(self, user_msg):
        type(self).LAST_INPUT = user_msg
        return _TurnHandle()


class _AsyncCodex:
    LAST_START_KWARGS: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def thread_start(self, **kwargs):
        type(self).LAST_START_KWARGS = kwargs
        return _Thread()


@pytest.fixture
def fake_codex(monkeypatch):
    mod = types.ModuleType("openai_codex")
    mod.AsyncCodex = _AsyncCodex
    mod.ApprovalMode = _ApprovalMode
    monkeypatch.setitem(sys.modules, "openai_codex", mod)
    yield mod


# ── tests ──────────────────────────────────────────────────────────────


def test_to_mcp_config_registers_bcli_server() -> None:
    from bcli.agent.backends._codex import MCP_SERVER_NAME, to_mcp_config

    cfg = to_mcp_config("finance")
    assert MCP_SERVER_NAME in cfg
    entry = cfg[MCP_SERVER_NAME]
    assert "command" in entry and "args" in entry
    assert entry["env"]["BCLI_PROFILE"] == "finance"


def test_factory_builds_codex_backend(fake_codex) -> None:
    from bcli.agent import get_agent_backend
    from bcli.agent.backends._codex import CodexBackend
    from bcli.config._model import AgentConfig

    backend = get_agent_backend(AgentConfig(backend="codex", model="gpt-5"))
    assert isinstance(backend, CodexBackend)
    assert backend.is_active is True


async def test_notification_mapping_and_final_answer(fake_codex) -> None:
    from bcli.agent.backends._codex import CodexBackend

    _TurnHandle.NOTIFICATIONS = [
        _Notification(item=_Item(type="assistant_message", text="Looking… ")),
        _Notification(item=_Item(type="mcp_tool_call", name="mcp__bcli__bcli_get",
                                 input={"endpoint": "vendors"}, id="i1")),
    ]
    _TurnHandle.RESULT = _TurnResult(final_response="There are 42 vendors.")

    backend = CodexBackend(model="gpt-5", max_turns=5)
    runtime = make_runtime()
    await backend.start_session(system_prompt="s", tools=ToolRegistry.default(), runtime=runtime)
    events = [ev async for ev in backend.send("how many vendors?")]
    await backend.close()

    kinds = [e.kind for e in events]
    assert "text_delta" in kinds
    assert "tool_call_started" in kinds
    assert kinds[-1] == "turn_complete"
    started = next(e for e in events if e.kind == "tool_call_started")
    assert started.tool_name == "bcli_get"  # mcp prefix stripped
    assert events[-1].text == "There are 42 vendors."


async def test_thread_start_passes_mcp_config_and_instructions(fake_codex) -> None:
    from bcli.agent.backends._codex import CodexBackend, MCP_SERVER_NAME

    _TurnHandle.NOTIFICATIONS = []
    _TurnHandle.RESULT = _TurnResult(final_response="ok")

    backend = CodexBackend(max_turns=5)
    runtime = make_runtime(profile=FakeProfile())
    runtime.profile_name = "sandbox"
    await backend.start_session(system_prompt="SYSTEM", tools=ToolRegistry.default(), runtime=runtime)
    _ = [ev async for ev in backend.send("hi")]
    await backend.close()

    kwargs = _AsyncCodex.LAST_START_KWARGS
    assert kwargs["base_instructions"] == "SYSTEM"
    assert MCP_SERVER_NAME in kwargs["config"]["mcp_servers"]


async def test_production_escalates_approval_mode(fake_codex) -> None:
    from bcli.agent.backends._codex import CodexBackend

    _TurnHandle.NOTIFICATIONS = []
    _TurnHandle.RESULT = _TurnResult(final_response="ok")

    backend = CodexBackend(max_turns=5)
    runtime = make_runtime(profile=FakeProfile(environment="production"))
    await backend.start_session(system_prompt="s", tools=ToolRegistry.default(), runtime=runtime)
    _ = [ev async for ev in backend.send("hi")]
    await backend.close()

    # Cautious posture → on_request, not the auto_review default.
    assert _AsyncCodex.LAST_START_KWARGS.get("approval_mode") == "on_request"
