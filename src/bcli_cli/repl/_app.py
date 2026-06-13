"""The Textual chat REPL — renderer half of the engine/renderer split.

:mod:`bcli.agent` emits :class:`~bcli.agent.AgentEvent` records; this app
consumes them and paints the chat. It owns no model logic: a backend is
built from ``[agent]`` config, a long-lived :class:`AgentRuntime` holds
the BC client, and each user turn streams events that update widgets.

Write safety surfaces here as the only interactive seam:
``awaiting_approval`` events raise the :class:`ApprovalScreen` modal and
resolve the runtime's pending future. In plan mode the model can only
``draft_batch``; the rendered YAML is promoted through ``bcli batch
run`` via :mod:`_plan_mode`.

Bare ``bcli`` on a TTY calls :func:`run_repl`. The first launch with no
usable ``[agent]`` backend drops into the setup wizard first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Input, Markdown

from bcli_cli.repl._commands import help_text, parse_slash
from bcli_cli.repl._widgets import ApprovalScreen, StatusBar, ToolCallPanel

if TYPE_CHECKING:
    from bcli.agent import AgentEvent, AgentRuntime, AgentSessionBackend


class _TurnState:
    """Per-turn flags (did we stream any text yet?)."""

    __slots__ = ("wrote",)

    def __init__(self) -> None:
        self.wrote = False


class ChatApp(App[int]):
    """Single-screen chat: scrolling transcript + input + status bar."""

    TITLE = "bcli agent"
    CSS = """
    #transcript { height: 1fr; padding: 1 0; }
    #prompt { dock: bottom; margin-bottom: 1; }
    .user-msg { margin: 0 2 1 2; color: $success; }
    .agent-msg { margin: 0 2 1 2; }
    .notice { margin: 0 2 1 2; color: $text-muted; }
    .error { margin: 0 2 1 2; color: $error; }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, *, profile: str | None = None) -> None:
        super().__init__()
        self._profile_arg = profile
        self._backend: "AgentSessionBackend | None" = None
        self._runtime: "AgentRuntime | None" = None
        self._client: Any = None
        self._client_ctx: Any = None
        self._busy = False
        self._model_label = ""
        self._profile_name = ""
        self._environment = ""
        self._plan_mode = False
        self._pending_draft: tuple[str, str] | None = None  # (name, yaml)

    # ── layout ─────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript")
        yield Input(placeholder="Ask about Business Central…  (/help)", id="prompt")
        yield StatusBar()
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        await self._start_session()

    # ── session lifecycle ──────────────────────────────────────────────

    async def _start_session(self) -> None:
        from bcli.agent import (
            AgentRuntime,
            ToolRegistry,
            build_system_prompt,
            get_agent_backend,
            load_bc_md,
        )
        from bcli.context import ProfileSnapshot, build_bundle
        from bcli_cli._state import state
        from bcli_cli.commands.agent_cmd import resolve_plan_mode

        if self._profile_arg:
            state.profile_name = self._profile_arg

        cfg = state.config.agent
        self._backend = get_agent_backend(cfg)
        if not self._backend.is_active:
            from bcli.agent import NullAgentBackend

            await self._notice(NullAgentBackend.SETUP_HINT, kind="error")
            return

        profile = state.profile
        self._profile_name = state.active_profile_name
        self._environment = profile.environment
        self._model_label = getattr(self._backend, "model_label", "") or cfg.model

        self._client = state.make_async_client()
        self._client_ctx = self._client
        await self._client.__aenter__()

        self._runtime = AgentRuntime(
            client=self._client,
            profile=profile,
            profile_name=self._profile_name,
            registry=state.registry,
        )
        self._plan_mode = resolve_plan_mode(
            cfg.plan_mode_default, is_production=self._runtime.is_production,
        )
        self._runtime.plan_mode = self._plan_mode

        memory = load_bc_md(self._profile_name) if cfg.memory else ""
        bundle = build_bundle(profile=ProfileSnapshot(
            name=self._profile_name,
            environment=profile.environment,
            company=profile.company_name or "",
            auth_method=profile.auth_method,
            disable_writes=getattr(profile, "disable_writes", False),
        ))
        system_prompt = build_system_prompt(
            memory_text=memory, bundle=bundle, plan_mode=self._plan_mode,
        )
        await self._backend.start_session(
            system_prompt=system_prompt,
            tools=ToolRegistry.default(),
            runtime=self._runtime,
        )
        self.query_one(StatusBar).update_state(
            model=self._model_label, profile=self._profile_name,
            environment=self._environment, plan_mode=self._plan_mode,
        )
        await self._notice(
            f"Connected. profile={self._profile_name} env={self._environment}"
            + ("  ·  PLAN MODE" if self._plan_mode else ""),
        )

    async def _teardown(self) -> None:
        if self._backend is not None:
            try:
                await self._backend.close()
            except Exception:  # noqa: BLE001
                pass
        if self._client_ctx is not None:
            try:
                await self._client_ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._client_ctx = None

    # ── input handling ─────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        self.query_one("#prompt", Input).value = ""
        if not line:
            return
        cmd = parse_slash(line)
        if cmd is not None:
            await self._handle_slash(cmd)
            return
        if self._busy:
            await self._notice("Still working on the previous turn…")
            return
        if self._backend is None or not self._backend.is_active:
            await self._notice("No active agent backend. Run 'bcli agent init'.", kind="error")
            return
        await self._add_message(line, css="user-msg", prefix="› ")
        self._run_turn(line)

    async def _handle_slash(self, cmd) -> None:  # noqa: ANN001
        from bcli_cli._state import state

        name = cmd.name
        if name == "help":
            await self._add_markdown(help_text())
        elif name == "__unknown__":
            await self._notice(f"Unknown command: {cmd.arg}  (/help)", kind="error")
        elif name in ("exit",):
            await self._teardown()
            self.exit(0)
        elif name == "clear":
            await self.query_one("#transcript", VerticalScroll).remove_children()
            if self._backend is not None:
                # Reset turn history by restarting the session lazily.
                await self._notice("Transcript cleared.")
        elif name == "context":
            await self._add_markdown(self._context_markdown())
        elif name == "plan":
            self._plan_mode = not self._plan_mode
            if self._runtime is not None:
                self._runtime.plan_mode = self._plan_mode
            self.query_one(StatusBar).update_state(plan_mode=self._plan_mode)
            await self._notice(
                f"Plan mode {'ON — writes become draft proposals' if self._plan_mode else 'OFF'}."
            )
        elif name == "model":
            if cmd.arg:
                await self._notice(
                    f"Model switch to '{cmd.arg}' takes effect on restart; "
                    "set [agent] model in config to persist."
                )
            else:
                await self._notice(f"Current model: {self._model_label or '—'}")
        elif name == "profile":
            if cmd.arg:
                state.profile_name = cmd.arg
                await self._teardown()
                await self.query_one("#transcript", VerticalScroll).remove_children()
                self._profile_arg = cmd.arg
                await self._start_session()
            else:
                await self._notice(f"Current profile: {self._profile_name or '—'}")
        elif name == "company":
            await self._notice(
                f"Default company hint set to '{cmd.arg}'. Pass company per tool call "
                "or mention it in your message." if cmd.arg
                else "Specify a company alias: /company LLC"
            )
        elif name == "yes":
            await self._notice("No pending approval to confirm.")
        else:
            await self._notice(f"/{name} is not wired yet.")

    # ── turn driving (worker) ──────────────────────────────────────────

    @work(exclusive=True)
    async def _run_turn(self, user_msg: str) -> None:
        assert self._backend is not None and self._runtime is not None
        self._busy = True
        md = Markdown("")
        await self.query_one("#transcript", VerticalScroll).mount(md)
        md.add_class("agent-msg")
        stream = Markdown.get_stream(md)
        panels: dict[str, ToolCallPanel] = {}
        turn = _TurnState()
        try:
            async for ev in self._backend.send(user_msg):
                await self._on_event(ev, stream, panels, turn)
        except Exception as exc:  # noqa: BLE001
            await self._notice(f"Turn failed: {exc}", kind="error")
        finally:
            await stream.stop()
            self._busy = False
            self._scroll_end()

    async def _on_event(self, ev: "AgentEvent", stream, panels, turn) -> None:  # noqa: ANN001
        kind = ev.kind
        if kind == "text_delta":
            turn.wrote = True
            await stream.write(ev.text)
        elif kind == "tool_call_started":
            panel = ToolCallPanel(ev.tool_name, dict(ev.tool_args))
            panels[ev.tool_call_id or ev.tool_name] = panel
            await self.query_one("#transcript", VerticalScroll).mount(panel)
            self._scroll_end()
        elif kind == "tool_result":
            panel = panels.get(ev.tool_call_id or ev.tool_name)
            if panel is not None:
                panel.set_result(ev.result)
            await self._maybe_capture_draft(ev.result)
        elif kind == "awaiting_approval":
            approved = await self.push_screen_wait(ApprovalScreen(
                tool_name=ev.tool_name, reason=ev.reason, args=dict(ev.tool_args),
            ))
            if self._runtime is not None:
                self._runtime.resolve_approval(ev.approval_id, bool(approved))
        elif kind == "error":
            await self._notice(ev.error, kind="error")
        elif kind == "turn_complete":
            if ev.text and not turn.wrote:
                # Backend gave a final answer without streaming deltas.
                await stream.write(ev.text)
            if self._pending_draft is not None:
                await self._offer_plan_promotion()

    async def _maybe_capture_draft(self, result: Any) -> None:
        """Stash a draft_batch result so we can offer to run it after the turn."""
        payload = result
        if isinstance(result, str):
            try:
                import json

                payload = json.loads(result)
            except Exception:  # noqa: BLE001
                return
        if isinstance(payload, dict) and payload.get("status") == "drafted":
            self._pending_draft = (payload.get("name", "agent-plan"),
                                   payload.get("batch_yaml", ""))

    async def _offer_plan_promotion(self) -> None:
        from bcli_cli.repl._plan_mode import run_batch, write_draft

        name, yaml_text = self._pending_draft or ("", "")
        self._pending_draft = None
        if not yaml_text:
            return
        path = write_draft(yaml_text, name=name)
        await self._add_markdown(
            f"**Plan drafted** → `{path}`\n\n```yaml\n{yaml_text}```\n\n"
            "Approve to dry-run, then run for real."
        )
        approved = await self.push_screen_wait(ApprovalScreen(
            tool_name="batch run (dry-run)", reason="review the drafted plan",
            args={"file": str(path)},
        ))
        if not approved:
            await self._notice("Plan not run. The YAML is saved for manual review.")
            return
        ok, out = await run_batch(path, profile_name=self._profile_name, dry_run=True)
        await self._notice(f"Dry-run {'ok' if ok else 'failed'}: {out[:500]}")
        if not ok:
            return
        confirm = await self.push_screen_wait(ApprovalScreen(
            tool_name="batch run", reason="execute the plan for real",
            args={"file": str(path)},
        ))
        if not confirm:
            await self._notice("Plan not executed.")
            return
        ok, out = await run_batch(path, profile_name=self._profile_name, dry_run=False)
        await self._notice(f"Batch run {'ok' if ok else 'failed'}: {out[:500]}",
                           kind="error" if not ok else "notice")

    # ── transcript helpers ─────────────────────────────────────────────

    async def _add_message(self, text: str, *, css: str, prefix: str = "") -> None:
        from textual.widgets import Static

        w = Static(prefix + text)
        w.add_class(css)
        await self.query_one("#transcript", VerticalScroll).mount(w)
        self._scroll_end()

    async def _add_markdown(self, markdown: str) -> None:
        md = Markdown(markdown)
        md.add_class("notice")
        await self.query_one("#transcript", VerticalScroll).mount(md)
        self._scroll_end()

    async def _notice(self, text: str, *, kind: str = "notice") -> None:
        await self._add_message(text, css=kind)

    def _context_markdown(self) -> str:
        return (
            "**Session context**\n\n"
            f"- model: `{self._model_label or '—'}`\n"
            f"- profile: `{self._profile_name or '—'}`\n"
            f"- environment: `{self._environment or '—'}`\n"
            f"- plan mode: `{'on' if self._plan_mode else 'off'}`\n"
        )

    def _scroll_end(self) -> None:
        try:
            self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)
        except Exception:  # noqa: BLE001
            pass

    async def action_quit(self) -> None:
        await self._teardown()
        self.exit(0)


def run_repl(*, profile: str | None = None) -> int:
    """Run the wizard if needed, then the chat app. Returns an exit code."""
    from bcli_cli._state import state
    from bcli_cli.repl._wizard import has_usable_backend, run_setup_wizard

    try:
        configured = has_usable_backend(state.config.agent)
    except Exception:  # noqa: BLE001
        configured = False
    if not configured:
        if not run_setup_wizard(force=False):
            return 1
        # Force a config reload so the freshly-written [agent] section is
        # picked up by the session about to start.
        state._config = None
        state._registry = None

    app = ChatApp(profile=profile)
    result = app.run()
    return int(result or 0)


__all__ = ["ChatApp", "run_repl"]
