"""``bcli agent`` — agent-mode commands.

* ``bcli agent run "<prompt>"`` — headless one-shot turn: stream the
  answer to stdout, tool activity to stderr. Testable without a TTY;
  the same engine the chat REPL uses.
* ``bcli agent init`` — (re)run the setup wizard that writes the
  ``[agent]`` config section.

The interactive chat REPL itself is launched by bare ``bcli`` on a TTY
(see ``bcli_cli.repl``).
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Optional

import typer
from rich.console import Console

from bcli_cli._state import state

app = typer.Typer(help="Agent mode — chat REPL engine and setup")

console = Console()
_stderr = Console(stderr=True)


def _agent_config(backend: str | None, model: str | None):
    from bcli.config._model import AgentConfig

    try:
        cfg = state.config.agent
    except Exception:  # noqa: BLE001
        cfg = AgentConfig()
    updates: dict = {}
    if backend:
        updates["backend"] = backend
    if model:
        updates["model"] = model
    return cfg.model_copy(update=updates) if updates else cfg


def resolve_plan_mode(
    plan_mode_default: str, *, is_production: bool,
    force_on: bool = False, force_off: bool = False,
) -> bool:
    """``auto`` = on-for-production; explicit flags win."""
    if force_on:
        return True
    if force_off:
        return False
    mode = (plan_mode_default or "auto").strip().lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    return is_production


@app.command("run")
def run_command(
    prompt: str = typer.Argument(..., help="One-shot prompt for the agent"),
    backend: Optional[str] = typer.Option(
        None, "--backend", help="One-shot backend override "
        "(pydantic-ai / claude-code / codex / module:Class)",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", help="One-shot model override (e.g. anthropic:claude-sonnet-4-5)",
    ),
    plan: bool = typer.Option(
        False, "--plan", help="Force plan mode on (writes become draft_batch)",
    ),
    no_plan: bool = typer.Option(
        False, "--no-plan", help="Force plan mode off",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Auto-approve gated writes (scripted use; be careful)",
    ),
) -> None:
    """Run one agent turn headlessly and print the streamed answer."""
    from bcli.agent import get_agent_backend

    cfg = _agent_config(backend, model)
    session = get_agent_backend(cfg)
    if not session.is_active:
        from bcli.agent import NullAgentBackend

        _stderr.print(f"[yellow]{NullAgentBackend.SETUP_HINT}[/yellow]")
        raise typer.Exit(code=1)

    # Consent gate for subscription-credential backends (claude-code /
    # codex without an API key). API-key auth never prompts.
    from bcli_cli.repl._consent import ensure_subscription_consent

    if not ensure_subscription_consent(cfg, interactive=sys.stdin.isatty()):
        raise typer.Exit(code=1)

    exit_code = asyncio.run(_drive(session, cfg, prompt, plan, no_plan, yes))
    if exit_code:
        raise typer.Exit(code=exit_code)


async def _drive(session, cfg, prompt: str, plan: bool, no_plan: bool, yes: bool) -> int:
    from bcli.agent import (
        AgentRuntime,
        ToolRegistry,
        build_system_prompt,
        load_bc_md,
    )
    from bcli.context import ProfileSnapshot, build_bundle

    profile = state.profile
    profile_name = state.active_profile_name
    client = state.make_async_client()

    async with client:
        runtime = AgentRuntime(
            client=client,
            profile=profile,
            profile_name=profile_name,
            registry=state.registry,
            auto_approve=yes,
        )
        runtime.plan_mode = resolve_plan_mode(
            cfg.plan_mode_default,
            is_production=runtime.is_production,
            force_on=plan, force_off=no_plan,
        )
        memory = load_bc_md(profile_name) if cfg.memory else ""
        bundle = build_bundle(profile=ProfileSnapshot(
            name=profile_name,
            environment=profile.environment,
            company=profile.company_name or "",
            auth_method=profile.auth_method,
            disable_writes=getattr(profile, "disable_writes", False),
        ))
        system_prompt = build_system_prompt(
            memory_text=memory, bundle=bundle, plan_mode=runtime.plan_mode,
        )
        await session.start_session(
            system_prompt=system_prompt,
            tools=ToolRegistry.default(),
            runtime=runtime,
        )
        had_error = False
        streamed_any = False
        try:
            async for ev in session.send(prompt):
                if ev.kind == "text_delta":
                    streamed_any = True
                    sys.stdout.write(ev.text)
                    sys.stdout.flush()
                elif ev.kind == "tool_call_started":
                    _stderr.print(
                        f"[dim]→ {ev.tool_name} "
                        f"{json.dumps(dict(ev.tool_args), default=str)}[/dim]"
                    )
                elif ev.kind == "tool_result":
                    _stderr.print(f"[dim]← {ev.tool_name} done[/dim]")
                elif ev.kind == "awaiting_approval":
                    approved = _prompt_approval(ev)
                    runtime.resolve_approval(ev.approval_id, approved)
                elif ev.kind == "error":
                    had_error = True
                    _stderr.print(f"[red]Error: {ev.error}[/red]")
                elif ev.kind == "turn_complete":
                    if not streamed_any and ev.text:
                        sys.stdout.write(ev.text)
                    sys.stdout.write("\n")
                    sys.stdout.flush()
        finally:
            await session.close()
        return 1 if had_error else 0


def _prompt_approval(ev) -> bool:
    """Headless approval: literal ``yes`` on a TTY, deny otherwise."""
    _stderr.print(
        f"[yellow]⚠ Write approval required — {ev.tool_name} "
        f"{json.dumps(dict(ev.tool_args), default=str)}[/yellow]\n"
        f"[yellow]  Reason: {ev.reason}[/yellow]"
    )
    if not sys.stdin.isatty():
        _stderr.print(
            "[red]✗ Denied: non-interactive session and --yes was not "
            "passed.[/red]"
        )
        return False
    answer = typer.prompt(
        "Type 'yes' to approve, anything else to deny",
        default="", show_default=False,
    )
    return answer.strip().lower() == "yes"


@app.command("init")
def init_command() -> None:
    """(Re)run the agent setup wizard — pick a backend, store the key."""
    from bcli_cli.repl._wizard import run_setup_wizard

    run_setup_wizard(force=True)


__all__ = ["app", "resolve_plan_mode", "run_command"]
