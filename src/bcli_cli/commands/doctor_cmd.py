"""``bcli doctor`` — self-rescue diagnostics for team installs.

The command runs a fixed set of independent checks and prints a one-screen
report. Non-zero exit when any check fails; ``--json`` flips to a structured
report for monitoring scripts. Designed to never raise — a totally broken
install must still produce readable output.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bcli._version import __version__
from bcli.config._defaults import (
    CONFIG_DIR,
    REGISTRIES_DIR,
    TOKEN_CACHE_FILE,
)
from bcli.diagnostics import CheckContext, CheckStatus, run_all_checks
from bcli_cli._state import state

console = Console()
err = Console(stderr=True)


def doctor_command(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Profile to inspect (default: active profile)"
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Emit a structured JSON report (machine-readable)"
    ),
    skip_network: bool = typer.Option(
        False, "--skip-network", help="Skip the BC connectivity probe"
    ),
) -> None:
    """Run diagnostics and print a one-screen verdict.

    Exit codes:
      0  all checks passed (some warnings allowed)
      1  one or more fail-level checks

    \b
    Examples:
      bcli doctor
      bcli doctor --profile finance
      bcli doctor --json | jq '.checks[] | select(.status=="fail")'
    """
    ctx = _build_context(profile_override=(profile or state.profile_name), skip_network=skip_network)
    results = run_all_checks(ctx)

    if output_json:
        _emit_json(ctx, results)
    else:
        _emit_text(ctx, results)

    if any(r.is_fail for r in results):
        raise typer.Exit(1)


# ─── Context construction ────────────────────────────────────────────


def _build_context(*, profile_override: str | None, skip_network: bool) -> CheckContext:
    """Build a CheckContext that tolerates a fully-broken install.

    Imports are inlined so that a config-loader crash still lets us emit a
    single fail result instead of crashing inside the CLI bootstrap.
    """
    config = None
    profile = None
    profile_name = profile_override or "default"

    try:
        from bcli.config import load_config

        config = load_config()
        profile_name = profile_override or config.defaults.profile
        try:
            profile = config.get_profile(profile_name)
        except Exception:  # noqa: BLE001 — captured by check_active_profile
            profile = None
    except Exception:  # noqa: BLE001 — captured by check_active_profile
        config = None

    bundle_dir = CONFIG_DIR / "bundles"

    return CheckContext(
        config=config,
        profile=profile,
        profile_name=profile_name,
        bundle_dir=bundle_dir,
        token_cache_path=TOKEN_CACHE_FILE,
        queries_dir=CONFIG_DIR / "queries",
        registries_dir=REGISTRIES_DIR,
        bcli_version=__version__,
        skip_network=skip_network,
    )


# ─── Rendering ───────────────────────────────────────────────────────


_STATUS_GLYPH = {
    CheckStatus.OK: ("[green]✓[/green]", "OK"),
    CheckStatus.WARN: ("[yellow]⚠[/yellow]", "WARN"),
    CheckStatus.FAIL: ("[red]✗[/red]", "FAIL"),
    CheckStatus.INFO: ("[dim]·[/dim]", "INFO"),
}


def _emit_text(ctx: CheckContext, results: list) -> None:
    err.print(
        f"[bold]bcli doctor[/bold] — profile: [cyan]{ctx.profile_name}[/cyan] "
        f"[dim](bcli {ctx.bcli_version})[/dim]"
    )
    err.print()

    fails = sum(1 for r in results if r.status is CheckStatus.FAIL)
    warns = sum(1 for r in results if r.status is CheckStatus.WARN)

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(justify="right", no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")

    for r in results:
        glyph, _ = _STATUS_GLYPH[r.status]
        table.add_row(glyph, r.name, r.summary)
        if r.hint and r.status in (CheckStatus.FAIL, CheckStatus.WARN):
            table.add_row("", "", f"[dim]hint: {r.hint}[/dim]")

    console.print(table)
    console.print()

    if fails:
        verdict = f"[bold red]Verdict: FAIL[/bold red] — {fails} failed, {warns} warnings"
    elif warns:
        verdict = f"[bold yellow]Verdict: WARN[/bold yellow] — {warns} warnings"
    else:
        verdict = "[bold green]Verdict: OK[/bold green]"
    console.print(verdict)


def _emit_json(ctx: CheckContext, results: list) -> None:
    payload = {
        "profile": ctx.profile_name,
        "bcli_version": ctx.bcli_version,
        "verdict": (
            "fail" if any(r.is_fail for r in results)
            else "warn" if any(r.status is CheckStatus.WARN for r in results)
            else "ok"
        ),
        "checks": [
            {
                "name": r.name,
                "status": r.status.value,
                "summary": r.summary,
                "hint": r.hint,
                "detail": r.detail,
            }
            for r in results
        ],
    }
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
