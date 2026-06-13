"""Textual pilot tests: drive ChatApp with a canned AgentEvent stream.

No model, no BC client — a fake backend yields a scripted event stream
and a fake runtime stands in for the write gate. Asserts the renderer
turns events into widgets and that the approval modal resolves the
runtime future.
"""

from __future__ import annotations

import pytest

from bcli.agent import AgentEvent
from bcli_cli.repl._app import ChatApp
from bcli_cli.repl._widgets import ApprovalScreen, ToolCallPanel


class FakeRuntime:
    """Minimal stand-in for AgentRuntime — records approvals."""

    def __init__(self) -> None:
        self.plan_mode = False
        self.is_production = False
        self.resolved: list[tuple[str, bool]] = []

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        self.resolved.append((approval_id, approved))
        return True


class ScriptedBackend:
    """AgentSessionBackend that replays a fixed list of events per turn."""

    is_active = True
    model_label = "scripted"

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events

    async def start_session(self, **_kw) -> None:  # noqa: ANN003
        ...

    async def send(self, user_msg: str):  # noqa: ANN201
        for ev in self._events:
            yield ev

    async def close(self) -> None:
        ...


def _make_app(events: list[AgentEvent], runtime: FakeRuntime | None = None) -> ChatApp:
    app = ChatApp()
    # Short-circuit _start_session: inject the fake backend + runtime so
    # the app never touches config / network.
    app._backend = ScriptedBackend(events)
    app._runtime = runtime or FakeRuntime()
    app._model_label = "scripted"
    app._profile_name = "test"
    app._environment = "sandbox"

    async def _noop_start() -> None:
        return None

    app._start_session = _noop_start  # type: ignore[assignment]
    return app


async def test_text_turn_renders_answer() -> None:
    events = [
        AgentEvent(kind="text_delta", text="There are "),
        AgentEvent(kind="text_delta", text="42 vendors."),
        AgentEvent(kind="turn_complete", text="There are 42 vendors."),
    ]
    app = _make_app(events)
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "how many vendors?"
        await pilot.press("enter")
        # Let the worker drain.
        for _ in range(8):
            await pilot.pause()
        from textual.widgets import Markdown

        markdowns = app.query(Markdown)
        combined = " ".join(m.source for m in markdowns)
        assert "42 vendors" in combined


async def test_tool_call_renders_panel() -> None:
    events = [
        AgentEvent(kind="tool_call_started", tool_name="bcli_get",
                   tool_call_id="c1", tool_args={"endpoint": "vendors"}),
        AgentEvent(kind="tool_result", tool_name="bcli_get",
                   tool_call_id="c1", result={"returned": 3}),
        AgentEvent(kind="turn_complete", text="done"),
    ]
    app = _make_app(events)
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "list vendors"
        await pilot.press("enter")
        for _ in range(6):
            await pilot.pause()
        panels = app.query(ToolCallPanel)
        assert len(panels) >= 1


async def test_slash_help_renders_without_backend_turn() -> None:
    app = _make_app([])
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "/help"
        await pilot.press("enter")
        await pilot.pause()
        from textual.widgets import Markdown

        combined = " ".join(m.source for m in app.query(Markdown))
        assert "/model" in combined


async def test_approval_modal_resolves_runtime_future() -> None:
    runtime = FakeRuntime()
    events = [
        AgentEvent(kind="awaiting_approval", approval_id="a1",
                   tool_name="bcli_post", reason="production target",
                   tool_args={"endpoint": "vendors"}),
        AgentEvent(kind="turn_complete", text="declined"),
    ]
    app = _make_app(events, runtime=runtime)
    async with app.run_test() as pilot:
        app.query_one("#prompt").value = "create a vendor"
        await pilot.press("enter")
        # Wait for the modal to appear.
        for _ in range(8):
            await pilot.pause()
            if isinstance(app.screen, ApprovalScreen):
                break
        assert isinstance(app.screen, ApprovalScreen)
        await pilot.press("n")  # decline
        for _ in range(6):
            await pilot.pause()
        assert runtime.resolved == [("a1", False)]
