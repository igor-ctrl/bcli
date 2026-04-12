"""bcapi auth — authentication commands."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from bcapi.auth._credentials import ClientCredentialsAuth
from bcapi.auth._token_cache import TokenCache
from bcapi_cli._state import state

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def login() -> None:
    """Authenticate and cache a token."""
    profile = state.profile

    console.print(f"[dim]Authenticating as {profile.client_id} against {profile.tenant_id}...[/dim]")

    try:
        auth = ClientCredentialsAuth(
            tenant_id=profile.tenant_id,
            client_id=profile.client_id or "",
            client_secret_env=profile.client_secret_env,
        )
        token = asyncio.run(auth.get_access_token())
        console.print(f"[green]✓[/green] Authenticated successfully")
        console.print(f"[dim]Token cached (length: {len(token)} chars)[/dim]")
    except Exception as e:
        console.print(f"[red]✗ Authentication failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show current token cache status."""
    profile = state.profile
    cache = TokenCache()

    cached = cache.get(profile.tenant_id, profile.client_id or "")
    if cached:
        console.print(f"[green]✓[/green] Valid cached token found")
        console.print(f"  Profile: {state.active_profile_name}")
        console.print(f"  Tenant: {profile.tenant_id}")
        console.print(f"  Client: {profile.client_id}")
    else:
        console.print("[yellow]No valid cached token.[/yellow] Run 'bcapi auth login'.")


@app.command()
def logout() -> None:
    """Clear cached tokens."""
    profile = state.profile
    cache = TokenCache()
    cache.clear(profile.tenant_id, profile.client_id)
    console.print(f"[green]✓[/green] Cleared tokens for profile '{state.active_profile_name}'")
