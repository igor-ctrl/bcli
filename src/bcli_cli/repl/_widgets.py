"""Textual widgets for the agent chat REPL.

* :class:`StatusBar` — model / profile / env / plan-mode indicator.
* :class:`ToolCallPanel` — a collapsible card showing one tool call and
  its result, updated in place as events arrive.
* :class:`ApprovalScreen` — the modal write-approval dialog; resolves an
  :class:`asyncio.Future` so the agent runtime's gate can continue.

These render :class:`~bcli.agent.AgentEvent` data; they hold no engine
logic. The app (:mod:`bcli_cli.repl._app`) owns the event loop and feeds
them.
"""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class StatusBar(Static):
    """One-line indicator of the active backend / profile / env / plan mode."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        model: str = "",
        profile: str = "",
        environment: str = "",
        plan_mode: bool = False,
    ) -> None:
        self._model = model
        self._profile = profile
        self._environment = environment
        self._plan_mode = plan_mode
        super().__init__(self._compose_text())

    def update_state(
        self,
        *,
        model: str | None = None,
        profile: str | None = None,
        environment: str | None = None,
        plan_mode: bool | None = None,
    ) -> None:
        if model is not None:
            self._model = model
        if profile is not None:
            self._profile = profile
        if environment is not None:
            self._environment = environment
        if plan_mode is not None:
            self._plan_mode = plan_mode
        self.update(self._compose_text())

    def _compose_text(self) -> str:
        parts = [
            f"model: {self._model or '—'}",
            f"profile: {self._profile or '—'}",
            f"env: {self._environment or '—'}",
        ]
        if self._plan_mode:
            parts.append("[bold yellow]PLAN MODE[/bold yellow]")
        return "  ·  ".join(parts) + "   (Ctrl+C to quit, /help for commands)"


class ToolCallPanel(Static):
    """A card for one tool call: name + args, then its result."""

    DEFAULT_CSS = """
    ToolCallPanel {
        border: round $accent;
        margin: 0 2 1 2;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._args = dict(tool_args or {})
        self._result_text = ""
        self._done = False
        super().__init__(self._renderable())

    def set_result(self, result: Any) -> None:
        self._result_text = _short_json(result)
        self._done = True
        self.update(self._renderable())

    def _renderable(self) -> str:
        marker = "✓" if self._done else "→"
        args = _short_json(self._args) if self._args else ""
        head = f"[bold]{marker} {self._tool_name}[/bold]"
        if args:
            head += f"  [dim]{args}[/dim]"
        body = f"\n[dim]{self._result_text}[/dim]" if self._result_text else ""
        return head + body


class ApprovalScreen(ModalScreen[bool]):
    """Modal write-approval dialog. Dismisses with True (approve) / False."""

    DEFAULT_CSS = """
    ApprovalScreen {
        align: center middle;
    }
    #approval-box {
        width: 70%;
        max-width: 90;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }
    #approval-title { text-style: bold; color: $warning; }
    #approval-buttons { height: auto; align: center middle; padding-top: 1; }
    Button { margin: 0 1; }
    """

    def __init__(self, *, tool_name: str, reason: str, args: dict[str, Any]) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._reason = reason
        self._args = dict(args or {})

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-box"):
            yield Label("Write approval required", id="approval-title")
            yield Static(f"\nTool: [bold]{self._tool_name}[/bold]")
            yield Static(f"Reason: {self._reason}")
            if self._args:
                yield Static(f"\n[dim]{_short_json(self._args, limit=400)}[/dim]")
            with Horizontal(id="approval-buttons"):
                yield Button("Approve (y)", variant="success", id="approve")
                yield Button("Decline (n)", variant="error", id="decline")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def on_key(self, event) -> None:  # noqa: ANN001
        if event.key in ("y", "Y"):
            self.dismiss(True)
        elif event.key in ("n", "N", "escape"):
            self.dismiss(False)


def _short_json(value: Any, *, limit: int = 200) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except Exception:  # noqa: BLE001
        text = str(value)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


__all__ = ["ApprovalScreen", "StatusBar", "ToolCallPanel"]
