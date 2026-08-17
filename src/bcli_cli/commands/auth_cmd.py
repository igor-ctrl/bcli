"""bcli auth — authentication commands."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.prompt import Prompt

from bcli.auth._credentials import ClientCredentialsAuth
from bcli.auth._msal_cache import MsalTokenCache
from bcli.auth._token_cache import TokenCache
from bcli.config._defaults import ENTRA_AUTHORITY_BASE
from bcli_cli._state import state

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def login(
    method: str | None = typer.Option(
        None, "--method", "-m",
        help="Auth method: browser, device, client_credentials (default: profile's auth_method)",
    ),
    incognito: bool = typer.Option(
        False, "--incognito", "-i",
        help="Open browser in incognito/private mode (fresh session, no cached login)",
    ),
) -> None:
    """Authenticate and cache a token.

    \b
    Examples:
      bcli auth login                         # uses profile's auth_method
      bcli auth login --method browser        # browser OAuth (user's BC permissions)
      bcli auth login --method device         # device code flow
      bcli auth login --method client_credentials  # service-to-service
    """
    profile = state.profile
    auth_method = _normalise_auth_method(method or profile.auth_method)

    if auth_method == "browser":
        console.print(f"[dim]Browser auth for tenant {profile.tenant_id}...[/dim]")
        from bcli.auth._browser import BrowserAuth

        auth = BrowserAuth(
            tenant_id=profile.tenant_id,
            client_id=profile.client_id or "",
            incognito=incognito,
        )
    elif auth_method == "device_code":
        console.print(f"[dim]Device code auth for tenant {profile.tenant_id}...[/dim]")
        from bcli.auth._device_code import DeviceCodeAuth

        auth = DeviceCodeAuth(
            tenant_id=profile.tenant_id,
            client_id=profile.client_id or "",
        )
    else:
        console.print(f"[dim]Authenticating as {profile.client_id} against {profile.tenant_id}...[/dim]")
        auth = ClientCredentialsAuth(
            tenant_id=profile.tenant_id,
            client_id=profile.client_id or "",
            client_secret_env=profile.client_secret_env,
        )

    from bcli.telemetry import events as _tev

    sink = state.telemetry
    capture_upn = state.config.telemetry.capture_user_upn
    try:
        token = asyncio.run(auth.get_access_token())
        console.print(f"[green]✓[/green] Authenticated successfully ({auth_method})")
        console.print(f"[dim]Token cached (length: {len(token)} chars)[/dim]")
        upn = ""
        if capture_upn:
            # Decode the JWT 'upn' claim if present. Best-effort — never fatal.
            try:
                import base64 as _b64
                import json as _json

                body = token.split(".")[1]
                body += "=" * (-len(body) % 4)
                claims = _json.loads(_b64.urlsafe_b64decode(body))
                upn = claims.get("upn") or claims.get("preferred_username") or ""
            except Exception:  # noqa: BLE001
                upn = ""
        sink.emit(*_tev.auth(method=auth_method, status="ok", user_upn=upn))
    except Exception as e:
        sink.emit(*_tev.auth(method=auth_method, status="error"))
        console.print(f"[red]✗ Authentication failed:[/red] {e}")
        raise typer.Exit(1)


def _normalise_auth_method(raw: str) -> str:
    """Return the canonical auth method or exit with a user-facing error."""
    method = raw.strip().lower().replace("-", "_")
    aliases = {
        "device": "device_code",
        "device_code": "device_code",
        "browser": "browser",
        "client_credentials": "client_credentials",
    }
    if method in aliases:
        return aliases[method]
    console.print(
        f"[red]Unsupported auth method '{raw}'.[/red] "
        "Use browser, device_code, or client_credentials."
    )
    raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show current token cache status."""
    profile = state.profile
    cache = TokenCache()

    cached = cache.get(profile.tenant_id, profile.client_id or "")
    if cached:
        console.print("[green]✓[/green] Valid cached token found")
        console.print(f"  Profile: {state.active_profile_name}")
        console.print(f"  Auth method: {profile.auth_method}")
        console.print(f"  Tenant: {profile.tenant_id}")
        console.print(f"  Client: {profile.client_id}")
    else:
        console.print("[yellow]No valid cached token.[/yellow] Run 'bcli auth login'.")

    # An expired access token is not the same as "you must sign in again" — if a
    # refresh token is persisted, the next command renews without a prompt. Say
    # so, otherwise the line above reads as more alarming than it is.
    if profile.auth_method in ("browser", "device_code"):
        has_account = MsalTokenCache().has_accounts(
            client_id=profile.client_id or "",
            authority=f"{ENTRA_AUTHORITY_BASE}/{profile.tenant_id}",
        )
        if has_account:
            console.print("  Silent renewal: [green]available[/green] (refresh token cached)")
        else:
            console.print(
                "  Silent renewal: [dim]unavailable[/dim] — next command prompts interactively"
            )

    # Show keychain status
    if ClientCredentialsAuth.has_keyring():
        from bcli.auth._credentials import _try_keyring_get, KEYRING_SERVICE

        keyring_key = f"{profile.tenant_id}:{profile.client_id}"
        has_secret = _try_keyring_get(KEYRING_SERVICE, keyring_key) is not None
        if has_secret:
            console.print("  Keychain: [green]secret stored[/green]")
        else:
            console.print("  Keychain: [dim]no secret stored[/dim] (run 'bcli auth store-secret')")
    else:
        console.print("  Keychain: [dim]keyring not installed[/dim] (pip install keyring)")


@app.command()
def logout() -> None:
    """Clear cached tokens."""
    profile = state.profile
    cache = TokenCache()
    cache.clear(profile.tenant_id, profile.client_id)

    # Also forget the persisted MSAL cache. Clearing only the access token
    # above would leave the refresh token on disk, so the next command would
    # renew silently and "logout" would be a lie.
    removed = MsalTokenCache().remove_accounts(
        client_id=profile.client_id or "",
        authority=f"{ENTRA_AUTHORITY_BASE}/{profile.tenant_id}",
    )

    console.print(f"[green]✓[/green] Cleared tokens for profile '{state.active_profile_name}'")
    if removed:
        console.print(
            f"  [dim]Signed out {removed} cached account(s); "
            f"next command will prompt.[/dim]"
        )


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
