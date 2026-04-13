"""Context banner and display helpers."""

from __future__ import annotations

from rich.console import Console

from bcli_cli._state import state

console = Console(stderr=True)


def print_context_banner() -> None:
    """Print the resolved context (profile, env, company) before output."""
    if state.quiet:
        return

    profile = state.profile
    parts = [
        f"profile: [bold]{state.active_profile_name}[/bold]",
        f"env: [cyan]{profile.environment}[/cyan]",
    ]
    if profile.company_name:
        parts.append(f"company: [green]{profile.company_name}[/green]")
    elif profile.company_id:
        parts.append(f"company: [green]{profile.company_id[:8]}...[/green]")

    banner = " | ".join(parts)
    console.print(f"[dim][{banner}][/dim]")
