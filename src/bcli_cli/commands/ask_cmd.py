"""``bcli ask`` — second-opinion oracle (Part 2).

Bundles the operator's recent failing context (last-error,
http-tail, profile, describe excerpt) via :mod:`bcli.context`,
ships it to a configured LLM backend, prints the answer.

Flags
-----
- ``--no-context`` — drop the auto-bundle; ask the model the question
  alone.
- ``--attach PATH`` — pin a file into the bundle (redacted +
  truncated).
- ``--backend NAME`` — one-shot backend override (``claude`` /
  ``openai`` / ``module:Class``).
- ``--dry-run`` — print the redacted bundle that would be sent; no
  network call.
- ``--include-bodies`` — include HTTP request/response bodies in the
  bundle (default off).
- ``--include-debug`` — include the ``last-error-debug.json``
  traceback sidecar (default off).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown

from bcli.ask import AskAnswer, collect_extra_context, get_asker
from bcli.config._model import AskConfig
from bcli.context import (
    BundlePolicy,
    ProfileSnapshot,
    TokenBudget,
    build_bundle,
    read_last_error,
)
from bcli_cli._state import state

console = Console()
_stderr = Console(stderr=True)
logger = logging.getLogger("bcli.ask")


def ask_command(
    question: str = typer.Argument(
        ..., help="Free-text question for the oracle"
    ),
    no_context: bool = typer.Option(
        False, "--no-context",
        help="Skip the auto-bundle; ask the model the question alone",
    ),
    attach: Optional[list[Path]] = typer.Option(
        None, "--attach",
        help="Pin a file into the bundle (redacted + truncated)",
    ),
    backend: Optional[str] = typer.Option(
        None, "--backend",
        help="One-shot backend override (e.g. claude / openai)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the redacted bundle that would be sent; no network",
    ),
    include_bodies: bool = typer.Option(
        False, "--include-bodies",
        help="Include HTTP request/response bodies in the bundle",
    ),
    include_debug: bool = typer.Option(
        False, "--include-debug",
        help="Include the last-error-debug.json traceback sidecar",
    ),
    max_tokens: Optional[int] = typer.Option(
        None, "--max-tokens",
        help="Override the bundle's token budget for this call",
    ),
) -> None:
    """Ask the oracle. Bundles recent context, ships it to an LLM,
    prints the answer."""
    cfg = _build_config(backend_override=backend)
    bundle_policy = BundlePolicy(
        include_bodies=include_bodies,
        include_describe=cfg.include_describe and not no_context,
        include_http_tail=cfg.include_http_tail and not no_context,
        include_debug=include_debug,
    )

    profile_snapshot = _profile_snapshot()
    raw_attachments: list[tuple[str, str]] = []
    for path in attach or []:
        try:
            raw_attachments.append((path.name, path.read_text(encoding="utf-8")))
        except OSError as exc:
            _stderr.print(
                f"[yellow]Could not read attachment {path}: {exc}[/yellow]"
            )

    # Resolve last-error explicitly so --no-context truly suppresses
    # it (bundle's default behaviour reads from disk when None).
    # --include-debug picks the traceback sidecar over the redacted
    # primary; the operator opted in to seeing it.
    le = None
    if not no_context and include_debug:
        le = read_last_error(debug=True)
    # recent_http honours --no-context via policy.include_http_tail
    # = False above; passing the empty tuple here makes the
    # suppression unambiguous (no implicit disk read).
    recent_http: tuple = () if no_context else None

    bundle = build_bundle(
        question=question,
        profile=profile_snapshot,
        policy=bundle_policy,
        budget=TokenBudget(max_tokens=max_tokens or 16_000),
        raw_attachments=tuple(raw_attachments),
        last_error=le,
        skip_last_error=no_context,
        recent_http=recent_http,
    )

    # Run any opted-in context providers (R8). These never auto-enable —
    # they only fire when the user lists them in [ask] context_providers.
    if cfg.context_providers and not no_context:
        try:
            extras = collect_extra_context(
                profile=profile_snapshot,
                last_error=bundle.last_error,
                enabled=cfg.context_providers,
            )
            if extras:
                _stderr.print(
                    f"[dim]Loaded {len(extras)} extra context fields from "
                    f"{len(cfg.context_providers)} provider(s).[/dim]"
                )
                # Append as a bundle attachment so it lands in the
                # prompt without bypassing the redaction layer.
                rendered = "\n".join(
                    f"- **{k}**: {v}" for k, v in extras.items()
                )
                bundle = build_bundle(
                    question=question,
                    profile=profile_snapshot,
                    policy=bundle_policy,
                    budget=TokenBudget(max_tokens=max_tokens or 16_000),
                    raw_attachments=tuple(raw_attachments) + (
                        ("context-providers.md", rendered),
                    ),
                    last_error=le,
                    skip_last_error=no_context,
                    recent_http=recent_http,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("context providers failed: %s", exc, exc_info=True)

    if dry_run:
        _print_dry_run(bundle)
        return

    asker = get_asker(cfg)
    if not asker.is_active:
        _stderr.print(
            "[yellow]No ask backend configured. Set [ask] backend = "
            "'claude' (or 'openai') in ~/.config/bcli/config.toml and "
            "install bc-cli[ask] (or bc-cli[ask-claude]).[/yellow]"
        )
        raise typer.Exit(code=1)

    try:
        answer: AskAnswer = asker.ask(question=question, bundle=bundle)
    except Exception as exc:  # noqa: BLE001
        _stderr.print(f"[red]Ask failed: {exc}[/red]")
        raise typer.Exit(code=1)

    if not answer.answer.strip():
        for w in answer.warnings:
            _stderr.print(f"[yellow]{w}[/yellow]")
        raise typer.Exit(code=1)

    console.print(Markdown(answer.answer))
    if state.verbose and answer.model:
        _stderr.print(
            f"\n[dim]model={answer.model} input_tokens={answer.input_tokens}"
            f" output_tokens={answer.output_tokens}[/dim]"
        )


# ─── Helpers ────────────────────────────────────────────────────────


def _build_config(*, backend_override: str | None) -> AskConfig:
    """Resolve the active :class:`AskConfig` with optional override."""
    try:
        cfg = state.config.ask
    except Exception:  # noqa: BLE001
        cfg = AskConfig()
    if backend_override:
        cfg = cfg.model_copy(update={"backend": backend_override})
    return cfg


def _profile_snapshot() -> ProfileSnapshot:
    """Build a :class:`ProfileSnapshot` from the active state."""
    try:
        cfg = state.config
        profile = cfg.get_profile(state.profile_name)
        return ProfileSnapshot(
            name=state.profile_name or cfg.defaults.profile,
            environment=state.env_override or profile.environment,
            company=state.company_override or (profile.company_name or ""),
            auth_method=profile.auth_method,
            disable_writes=profile.disable_writes,
        )
    except Exception:  # noqa: BLE001
        return ProfileSnapshot()


def _print_dry_run(bundle) -> None:
    """Print the bundle that would be sent."""
    console.print("[bold]Dry-run bundle (no network call)[/bold]")
    console.print()
    console.print(Markdown(bundle.to_prompt_text()))
    console.print()
    console.print(
        f"[dim]Bundle: ~{bundle.budget.actual_tokens} tokens "
        f"({len(bundle.sources)} source(s), "
        f"{len(bundle.redactions)} redaction(s))[/dim]"
    )


__all__ = ["ask_command"]
