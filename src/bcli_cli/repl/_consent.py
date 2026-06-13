"""Subscription-auth consent gate (claude-code / codex backends).

Riding a personal Claude or ChatGPT subscription from a third-party app
is *individual-use* territory at both vendors: Anthropic's per-plan
Agent SDK credit explicitly covers third-party apps but is sized for one
person; Codex subscription auth uses an undocumented endpoint with 5-hour
rate windows. Teams must use API keys.

This gate therefore fires only when:

1. the configured backend is ``claude-code`` or ``codex``, AND
2. no API key is detectable (subscription credentials only), AND
3. consent was not already persisted.

The user must type the literal ``yes``. Consent is persisted as
``subscription_authorized = true`` + timestamp under ``[agent]`` in the
global config via tomlkit — visible in a plain-text file and revocable
by deleting the line. API-key auth never prompts.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from bcli.config._model import AgentConfig

_stderr = Console(stderr=True)


_CONSENT_TEXT = {
    "claude-code": (
        "The claude-code backend found no ANTHROPIC_API_KEY — it would "
        "run on your personal Claude subscription (Agent SDK credit).\n\n"
        "  • This credit is sized for individual use. Anthropic requires "
        "teams and shared deployments to use API keys.\n"
        "  • Your subscription's rate limits apply to everything bcli "
        "does here.\n\n"
        "If this is your own machine and your own subscription, you may "
        "authorize it. Otherwise set ANTHROPIC_API_KEY (or run "
        "'bcli agent init' and pick the API-key path)."
    ),
    "codex": (
        "The codex backend found no CODEX_API_KEY / OPENAI_API_KEY — it "
        "would run on your ChatGPT subscription login "
        "(~/.codex/auth.json).\n\n"
        "  • Subscription access for third-party apps uses an "
        "undocumented endpoint with 5-hour rate windows; OpenAI's "
        "sanctioned programmatic path is an API key.\n"
        "  • Your subscription's rate limits apply to everything bcli "
        "does here.\n\n"
        "If this is your own machine and your own subscription, you may "
        "authorize it. Otherwise set CODEX_API_KEY (or run "
        "'bcli agent init' and pick the API-key path)."
    ),
}


def needs_consent(cfg: "AgentConfig") -> bool:
    """True when the consent gate must run before starting a session."""
    backend = (cfg.backend or "").strip()
    if backend not in _CONSENT_TEXT:
        return False
    if cfg.subscription_authorized:
        return False
    from bcli.agent._auth_detect import detect_claude_auth, detect_codex_auth

    detect = detect_claude_auth if backend == "claude-code" else detect_codex_auth
    return detect() == "subscription"


def persist_consent() -> None:
    """Record consent (flag + UTC timestamp) in the global config."""
    from bcli.config._loader import update_config_section

    update_config_section("agent", {
        "subscription_authorized": True,
        "subscription_authorized_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
    })


def ensure_subscription_consent(
    cfg: "AgentConfig",
    *,
    interactive: bool | None = None,
    input_func=input,
) -> bool:
    """Run the consent gate if needed. Returns True when OK to proceed.

    Non-interactive sessions can never grant consent — they get a hint
    and ``False``. ``input_func`` is injectable for tests and for the
    Textual wizard, which collects the answer through its own widget.
    """
    if not needs_consent(cfg):
        return True

    backend = (cfg.backend or "").strip()
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        _stderr.print(
            "[red]✗ Subscription authorization required for the "
            f"'{backend}' backend. Run 'bcli agent init' once "
            "interactively to authorize, or configure an API key.[/red]"
        )
        return False

    _stderr.print(f"[yellow]{_CONSENT_TEXT[backend]}[/yellow]")
    try:
        answer = input_func(
            "Type 'yes' to authorize subscription use (anything else cancels): "
        )
    except (EOFError, KeyboardInterrupt):
        return False
    if answer.strip() != "yes":
        _stderr.print("[red]✗ Not authorized.[/red]")
        return False

    persist_consent()
    _stderr.print(
        "[green]✓ Authorized. Recorded in ~/.config/bcli/config.toml "
        "([agent] subscription_authorized) — delete the line to "
        "revoke.[/green]"
    )
    return True


__all__ = ["ensure_subscription_consent", "needs_consent", "persist_consent"]
