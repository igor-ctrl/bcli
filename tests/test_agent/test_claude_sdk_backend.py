"""Claude Code backend — mocked claude-agent-sdk (package not installed).

A fake ``claude_agent_sdk`` module is injected into ``sys.modules`` so the
backend's lazy imports resolve to controllable stand-ins. Asserts the
AgentEvent translation (TextBlock → text_delta, ToolUseBlock →
tool_call_started, ResultMessage → turn_complete), the can_use_tool fence
(allow bcli MCP tools, deny others), and the streaming-mode dummy hook.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

from _helpers import make_runtime

from bcli.agent import ToolRegistry


# ── fake claude_agent_sdk ──────────────────────────────────────────────


@dataclass
class _TextBlock:
    text: str


@dataclass
class _ToolUseBlock:
    name: str
    id: str
    input: dict


@dataclass
class _ToolResultBlock:
    tool_use_id: str
    content: Any


@dataclass
class _AssistantMessage:
    content: list


@dataclass
class _ResultMessage:
    result: str = ""
    subtype: str = "success"


@dataclass
class _PermissionResultAllow:
    updated_input: dict | None = None
    behavior: str = "allow"


@dataclass
class _PermissionResultDeny:
    message: str = ""
    behavior: str = "deny"


@dataclass
class _HookMatcher:
    hooks: list = field(default_factory=list)
    matcher: Any = None


@dataclass
class _ClaudeAgentOptions:
    system_prompt: str = ""
    mcp_servers: dict = field(default_factory=dict)
    allowed_tools: list = field(default_factory=list)
    can_use_tool: Any = None
    hooks: dict = field(default_factory=dict)
    max_turns: int = 20
    model: str = ""


class _FakeClient:
    """Replays a scripted message list from receive_response()."""

    SCRIPT: list = []

    def __init__(self, *, options) -> None:
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        # Drain the async-iterable prompt to mimic the SDK's streaming mode.
        if hasattr(prompt, "__aiter__"):
            async for _ in prompt:
                pass

    async def receive_response(self):
        for msg in type(self).SCRIPT:
            yield msg


def _tool_decorator(name, description, input_schema, annotations=None):
    def wrap(fn):
        fn._tool_name = name
        return fn
    return wrap


def _create_sdk_mcp_server(*, name, version, tools):
    return {"name": name, "version": version, "tools": tools}


@pytest.fixture
def fake_sdk(monkeypatch):
    mod = types.ModuleType("claude_agent_sdk")
    mod.ClaudeSDKClient = _FakeClient
    mod.ClaudeAgentOptions = _ClaudeAgentOptions
    mod.HookMatcher = _HookMatcher
    mod.create_sdk_mcp_server = _create_sdk_mcp_server
    mod.tool = _tool_decorator
    mod.AssistantMessage = _AssistantMessage
    mod.ResultMessage = _ResultMessage
    mod.TextBlock = _TextBlock
    mod.ToolUseBlock = _ToolUseBlock
    mod.ToolResultBlock = _ToolResultBlock
    mod.PermissionResultAllow = _PermissionResultAllow
    mod.PermissionResultDeny = _PermissionResultDeny
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    yield mod


# ── tests ──────────────────────────────────────────────────────────────


def test_factory_builds_claude_backend(fake_sdk) -> None:
    from bcli.agent import get_agent_backend
    from bcli.config._model import AgentConfig

    backend = get_agent_backend(AgentConfig(backend="claude-code", model="claude-x"))
    from bcli.agent.backends._claude_sdk import ClaudeCodeBackend

    assert isinstance(backend, ClaudeCodeBackend)
    assert backend.is_active is True


async def test_event_translation(fake_sdk) -> None:
    from bcli.agent.backends._claude_sdk import ClaudeCodeBackend

    _FakeClient.SCRIPT = [
        _AssistantMessage(content=[_TextBlock(text="There are ")]),
        _AssistantMessage(content=[
            _ToolUseBlock(name="mcp__bcli__bcli_get", id="t1",
                          input={"endpoint": "vendors"}),
        ]),
        _AssistantMessage(content=[_TextBlock(text="42 vendors.")]),
        _ResultMessage(result="There are 42 vendors."),
    ]
    backend = ClaudeCodeBackend(model="claude-x", max_turns=5)
    runtime = make_runtime()
    await backend.start_session(system_prompt="s", tools=ToolRegistry.default(), runtime=runtime)
    events = [ev async for ev in backend.send("how many vendors?")]
    await backend.close()

    kinds = [e.kind for e in events]
    assert "text_delta" in kinds
    assert "tool_call_started" in kinds
    assert kinds[-1] == "turn_complete"
    started = next(e for e in events if e.kind == "tool_call_started")
    # MCP prefix stripped for display.
    assert started.tool_name == "bcli_get"
    assert events[-1].text == "There are 42 vendors."


async def test_can_use_tool_fence(fake_sdk) -> None:
    from bcli.agent.backends._claude_sdk import ClaudeCodeBackend

    backend = ClaudeCodeBackend(max_turns=5)
    runtime = make_runtime()
    await backend.start_session(system_prompt="s", tools=ToolRegistry.default(), runtime=runtime)
    options = backend._build_options()

    can_use = options.can_use_tool
    allow = await can_use("mcp__bcli__bcli_get", {"endpoint": "vendors"}, None)
    assert allow.behavior == "allow"
    deny = await can_use("Bash", {"command": "rm -rf /"}, None)
    assert deny.behavior == "deny"


async def test_options_have_dummy_pre_tool_hook(fake_sdk) -> None:
    from bcli.agent.backends._claude_sdk import ClaudeCodeBackend

    backend = ClaudeCodeBackend(max_turns=5)
    runtime = make_runtime()
    await backend.start_session(system_prompt="s", tools=ToolRegistry.default(), runtime=runtime)
    options = backend._build_options()

    assert "PreToolUse" in options.hooks
    hook = options.hooks["PreToolUse"][0].hooks[0]
    result = await hook({}, "tid", None)
    assert result == {"continue_": True}


async def test_allowed_tools_are_only_bcli(fake_sdk) -> None:
    from bcli.agent.backends._claude_sdk import ClaudeCodeBackend

    backend = ClaudeCodeBackend(max_turns=5)
    runtime = make_runtime()
    await backend.start_session(system_prompt="s", tools=ToolRegistry.default(), runtime=runtime)
    options = backend._build_options()

    assert all(t.startswith("mcp__bcli__") for t in options.allowed_tools)
    assert "mcp__bcli__bcli_get" in options.allowed_tools
