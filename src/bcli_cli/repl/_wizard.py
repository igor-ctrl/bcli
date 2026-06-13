"""First-run setup wizard for ``bcli`` agent mode.

Reachable two ways:

* automatically, the first time bare ``bcli`` is launched on a TTY with
  no usable ``[agent]`` backend configured;
* explicitly, via ``bcli agent init``.

The wizard detects which backends are available on this machine
(installed ``claude`` / ``codex`` binaries, subscription logins, API
keys in the environment), lets the operator pick one, stores any API key
in the OS keychain, and writes the ``[agent]`` section to the global
config with tomlkit (preserving comments + unrelated sections).

The interactive shell is intentionally plain rich prompts, not Textual:
it must work the same whether reached from ``bcli agent init`` in a bare
terminal or from the REPL's startup path. The pure decision logic
(:func:`detect_backends`, :func:`build_agent_section`) is split out so it
is unit-testable without a TTY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from bcli.config._model import AgentConfig

console = Console()
_stderr = Console(stderr=True)


# ── backend options ────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackendOption:
    """One pickable backend in the wizard."""

    key: str               # menu key the user types
    backend: str           # value written to [agent] backend
    label: str             # human description
    needs_key: bool = False        # prompt for + store an API key
    key_provider: str = ""         # keyring namespace (llm:<provider>)
    default_model: str = ""        # written to [agent] model
    subscription: bool = False     # consent gate applies
    available: bool = True         # detected on this machine
    note: str = ""                 # extra guidance shown in the menu
    extra_hint: str = ""           # pip extra to install when missing


def detect_backends() -> list[BackendOption]:
    """Enumerate the backend choices, flagged with what's available here.

    Pure detection (PATH + env + ``~/.codex`` / ``~/.claude`` probes via
    :mod:`bcli.agent._auth_detect`); no heavy SDK imports.
    """
    from bcli.agent._auth_detect import (
        claude_code_available,
        codex_available,
        detect_claude_auth,
        detect_codex_auth,
    )

    claude_auth = detect_claude_auth()
    codex_auth = detect_codex_auth()

    return [
        BackendOption(
            key="1",
            backend="pydantic-ai",
            label="Anthropic API key (Claude) — recommended for teams",
            needs_key=True,
            key_provider="anthropic",
            default_model="anthropic:claude-sonnet-4-5",
            extra_hint="bc-cli[agent]",
        ),
        BackendOption(
            key="2",
            backend="pydantic-ai",
            label="OpenAI API key (GPT)",
            needs_key=True,
            key_provider="openai",
            default_model="openai:gpt-5",
            extra_hint="bc-cli[agent]",
        ),
        BackendOption(
            key="3",
            backend="pydantic-ai",
            label="Local model (Ollama / OpenAI-compatible) — no API key",
            default_model="ollama:llama3.1",
            note="uses base_url, defaults to http://localhost:11434/v1",
            extra_hint="bc-cli[agent]",
        ),
        BackendOption(
            key="4",
            backend="claude-code",
            label="Installed Claude Code CLI",
            subscription=(claude_auth == "subscription"),
            available=claude_code_available(),
            note=("subscription login detected — consent required"
                  if claude_auth == "subscription"
                  else "uses ANTHROPIC_API_KEY"
                  if claude_auth == "api_key" else "not signed in"),
            extra_hint="bc-cli[agent-claude-code]",
        ),
        BackendOption(
            key="5",
            backend="codex",
            label="Installed Codex CLI",
            subscription=(codex_auth == "subscription"),
            available=codex_available(),
            note=("subscription login detected — consent required"
                  if codex_auth == "subscription"
                  else "uses CODEX_API_KEY / OPENAI_API_KEY"
                  if codex_auth == "api_key" else "not signed in"),
            extra_hint="bc-cli[agent-codex]",
        ),
    ]


# ── config assembly (pure) ─────────────────────────────────────────────


@dataclass
class WizardResult:
    """Outcome of the wizard, ready to persist."""

    agent_section: dict = field(default_factory=dict)
    stored_key_provider: str = ""
    chosen: BackendOption | None = None


def build_agent_section(
    option: BackendOption, *, base_url: str = "", model: str = "",
) -> dict:
    """Build the ``[agent]`` config dict for a chosen backend option."""
    section: dict = {"backend": option.backend}
    resolved_model = (model or option.default_model).strip()
    if resolved_model:
        section["model"] = resolved_model
    if base_url:
        section["base_url"] = base_url
    return section


def has_usable_backend(cfg: "AgentConfig | None") -> bool:
    """True when ``[agent]`` names a non-null backend (wizard not needed)."""
    if cfg is None:
        return False
    backend = (cfg.backend or "").strip().lower()
    return bool(backend) and backend != "null"


# ── interactive flow ───────────────────────────────────────────────────


def run_setup_wizard(*, force: bool = False, input_func=None) -> bool:
    """Run the interactive wizard. Returns True when an agent is configured.

    ``force`` re-runs even when a backend is already set (``bcli agent
    init``). ``input_func`` is injectable for tests; defaults to rich
    prompts.
    """
    from bcli.config._loader import update_config_section
    from bcli_cli._state import state

    if not force:
        try:
            if has_usable_backend(state.config.agent):
                return True
        except Exception:  # noqa: BLE001
            pass

    options = detect_backends()
    _print_menu(options)

    choice = _ask(input_func, "Pick a backend [1-5]", default="1")
    option = next((o for o in options if o.key == choice.strip()), None)
    if option is None:
        _stderr.print("[red]Unknown choice — aborting setup.[/red]")
        return False

    if not option.available:
        _stderr.print(
            f"[red]{option.label} is not available on this machine "
            f"(install it, then re-run 'bcli agent init').[/red]"
        )
        return False

    base_url = ""
    model = ""
    if option.backend == "pydantic-ai" and not option.needs_key:
        # Local / OpenAI-compatible.
        base_url = _ask(
            input_func, "Base URL", default="http://localhost:11434/v1",
        ).strip()
        model = _ask(
            input_func, "Model name", default=option.default_model,
        ).strip()

    if option.needs_key:
        from bcli.agent.backends._pydantic_ai import store_llm_key

        key = _ask(
            input_func,
            f"{option.key_provider.title()} API key "
            "(stored in your OS keychain)",
            password=True,
        ).strip()
        if key:
            if store_llm_key(option.key_provider, key):
                console.print(
                    f"[green]Stored {option.key_provider} key in the OS "
                    "keychain.[/green]"
                )
            else:
                _stderr.print(
                    "[yellow]Could not reach the OS keychain. Set the key "
                    f"in the environment instead "
                    f"({_env_for(option.key_provider)}).[/yellow]"
                )

    section = build_agent_section(option, base_url=base_url, model=model)
    update_config_section("agent", section)
    console.print(
        f"[green]Configured [agent] backend = '{option.backend}'"
        + (f" model = '{section['model']}'" if section.get("model") else "")
        + ".[/green]"
    )

    # Subscription consent (claude-code / codex on a subscription login).
    if option.subscription:
        from bcli.config._model import AgentConfig
        from bcli_cli.repl._consent import ensure_subscription_consent

        cfg = AgentConfig(backend=option.backend)
        if not ensure_subscription_consent(
            cfg, interactive=True,
            input_func=(input_func or input),
        ):
            return False

    return True


def _env_for(provider: str) -> str:
    return {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }.get(provider, f"{provider.upper()}_API_KEY")


def _print_menu(options: list[BackendOption]) -> None:
    console.print("\n[bold]Set up bcli agent mode[/bold]")
    console.print(
        "Pick how the agent talks to an LLM. You can change this later "
        "in ~/.config/bcli/config.toml or with 'bcli agent init'.\n"
    )
    for o in options:
        status = "" if o.available else " [dim](not installed)[/dim]"
        note = f" [dim]— {o.note}[/dim]" if o.note else ""
        console.print(f"  [cyan]{o.key}[/cyan]. {o.label}{status}{note}")
    console.print("")


def _ask(input_func, prompt: str, *, default: str = "", password: bool = False) -> str:
    if input_func is not None:
        return input_func(prompt)
    from rich.prompt import Prompt

    return Prompt.ask(prompt, default=default or None, password=password) or default


__all__ = [
    "BackendOption",
    "WizardResult",
    "build_agent_section",
    "detect_backends",
    "has_usable_backend",
    "run_setup_wizard",
]
