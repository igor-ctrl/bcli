"""bcli auth — authentication commands."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.prompt import Prompt

from bcli.auth._credentials import ClientCredentialsAuth
from bcli.auth._token_cache import TokenCache
from bcli_cli._state import state

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
        console.print("[yellow]No valid cached token.[/yellow] Run 'bcli auth login'.")

    # Show keychain status
    if ClientCredentialsAuth.has_keyring():
        from bcli.auth._credentials import _try_keyring_get, KEYRING_SERVICE

        keyring_key = f"{profile.tenant_id}:{profile.client_id}"
        has_secret = _try_keyring_get(KEYRING_SERVICE, keyring_key) is not None
        if has_secret:
            console.print(f"  Keychain: [green]secret stored[/green]")
        else:
            console.print(f"  Keychain: [dim]no secret stored[/dim] (run 'bcli auth store-secret')")
    else:
        console.print(f"  Keychain: [dim]keyring not installed[/dim] (pip install keyring)")


@app.command()
def logout() -> None:
    """Clear cached tokens."""
    profile = state.profile
    cache = TokenCache()
    cache.clear(profile.tenant_id, profile.client_id)
    console.print(f"[green]✓[/green] Cleared tokens for profile '{state.active_profile_name}'")


@app.command("store-secret")
def store_secret() -> None:
    """Store client secret in the OS keychain (macOS Keychain, Windows Credential Manager)."""
    profile = state.profile

    if not ClientCredentialsAuth.has_keyring():
        console.print("[red]keyring library not installed.[/red]")
        console.print("[dim]Install it: pip install keyring[/dim]")
        raise typer.Exit(1)

    secret = Prompt.ask("Client secret", password=True)

    if ClientCredentialsAuth.store_secret(
        profile.tenant_id, profile.client_id or "", secret
    ):
        console.print(f"[green]✓[/green] Secret stored in OS keychain for profile '{state.active_profile_name}'")
        console.print("[dim]You can now remove client_secret_env from your config if you want.[/dim]")
    else:
        console.print("[red]✗ Failed to store secret in keychain.[/red]")
        raise typer.Exit(1)


@app.command("delete-secret")
def delete_secret() -> None:
    """Remove client secret from the OS keychain."""
    profile = state.profile

    if ClientCredentialsAuth.delete_secret(profile.tenant_id, profile.client_id or ""):
        console.print(f"[green]✓[/green] Secret removed from keychain for profile '{state.active_profile_name}'")
    else:
        console.print("[yellow]No secret found in keychain (or keyring not available).[/yellow]")
