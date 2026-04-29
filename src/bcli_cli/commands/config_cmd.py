"""bcli config — configuration management commands."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from bcli._url import build_companies_url
from bcli.auth._credentials import ClientCredentialsAuth
from bcli.client._transport import BCTransport
from bcli.config import BCProfile, load_config, save_config
from bcli.config._defaults import CONFIG_FILE
from bcli_cli._state import state

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def init(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p",
        help="Profile name (skips the interactive prompt)",
    ),
    scoped: bool = typer.Option(
        False, "--scoped",
        help="Sandboxed-domain mode for non-developer users: device-code "
             "auth (no client secret) and only the endpoints you --import "
             "are visible. The standard v2.0 catalog is hidden.",
    ),
    category: Optional[list[str]] = typer.Option(
        None, "--category",
        help="Restrict the profile to one or more endpoint categories "
             "(repeatable). Combine with --scoped to bound the user to a "
             "specific domain (e.g. --category warehouse --category sales).",
    ),
    import_file: Optional[Path] = typer.Option(
        None, "--import",
        help="After saving the profile, import endpoints from this file "
             "(.json or Postman v2.1 collection)",
    ),
) -> None:
    """Interactive setup wizard — configure a profile and discover companies.

    \b
    Examples:
      bcli config init                                       # standard wizard
      bcli config init --profile myteam --scoped --import endpoints.json
      bcli config init --profile myteam --scoped \\
          --category warehouse --import warehouse.postman_collection.json
    """
    console.print("[bold]bcli config init[/bold]\n")
    if scoped:
        bullets = [
            "device-code auth (corporate login, no client secret)",
            "standard v2.0 catalog hidden — only imported endpoints are visible",
        ]
        if category:
            bullets.append(f"endpoints filtered to category: {', '.join(category)}")
        console.print("[dim]Sandboxed mode:[/dim]")
        for b in bullets:
            console.print(f"[dim]  • {b}[/dim]")
        console.print()

    profile_name = profile or Prompt.ask("Profile name", default="default")
    tenant_id = Prompt.ask("Tenant ID (Azure AD)")
    environment = Prompt.ask("Environment name", default="Production")
    client_id = Prompt.ask("Client ID (App Registration)")

    # Secret handling — only relevant for client_credentials auth.
    secret_env = None
    if scoped:
        console.print(
            "[dim]Skipping client secret — device-code auth uses your "
            "corporate login.[/dim]"
        )
    elif ClientCredentialsAuth.has_keyring():
        use_keychain = Confirm.ask("Store client secret in OS keychain? (recommended)", default=True)
        if use_keychain:
            secret_value = Prompt.ask("Client secret", password=True)
            ClientCredentialsAuth.store_secret(tenant_id, client_id, secret_value)
            console.print("[green]✓[/green] Secret stored in OS keychain")
        else:
            secret_env = Prompt.ask(
                "Environment variable name holding the secret (not the secret itself)",
                default="BCLI_SECRET",
            )
    else:
        secret_env = Prompt.ask(
            "Environment variable name holding the secret (not the secret itself)",
            default="BCLI_SECRET",
        )

    # Build profile
    profile_obj = BCProfile(
        tenant_id=tenant_id,
        environment=environment,
        auth_method="device_code" if scoped else "client_credentials",
        client_id=client_id,
        client_secret_env=secret_env,
        disable_standard_api=scoped,
        allowed_categories=list(category) if category else [],
    )

    # Try to authenticate and discover companies. In scoped mode the user has
    # to complete a browser device flow first, so we ask before launching it.
    skip_discovery = scoped and not Confirm.ask(
        "Authenticate now via device code to auto-discover companies?",
        default=True,
    )
    if not skip_discovery:
        console.print("\n[dim]Authenticating...[/dim]")
        try:
            companies = asyncio.run(
                _discover_via_auth(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    environment=environment,
                    secret_env=secret_env,
                    use_device_code=scoped,
                )
            )

            if companies:
                console.print(f"[green]✓[/green] Found {len(companies)} company(ies)\n")

                table = Table(show_header=True)
                table.add_column("#", style="dim")
                table.add_column("Company Name")
                table.add_column("Company ID")

                for i, company in enumerate(companies, 1):
                    table.add_row(
                        str(i),
                        company.get("name", ""),
                        company.get("id", ""),
                    )
                console.print(table)

                if len(companies) == 1:
                    choice = 1
                else:
                    choice_str = Prompt.ask(
                        "\nSelect default company",
                        default="1",
                    )
                    choice = int(choice_str)

                selected = companies[choice - 1]
                profile_obj.company_id = selected.get("id", "")
                profile_obj.company_name = selected.get("name", "")
                console.print(f"[green]✓[/green] Selected: {profile_obj.company_name}")
            else:
                console.print("[yellow]No companies found. You can set company_id manually later.[/yellow]")

        except Exception as e:
            console.print(f"[yellow]⚠ Could not connect: {e}[/yellow]")
            console.print("[dim]Saving config anyway — you can test the connection later.[/dim]")
    else:
        console.print(
            "[dim]Skipped. Run 'bcli auth login --profile " + profile_name +
            "' then 'bcli company list' to set the default company.[/dim]"
        )

    # Save config — load existing to preserve other profiles
    config = load_config()
    config.defaults.profile = profile_name
    config.profiles[profile_name] = profile_obj
    path = save_config(config)
    state.config = config
    state._registry = None  # force registry reload with the new profile's settings

    console.print(f"\n[green]✓[/green] Config saved to {path}")
    if scoped:
        console.print(
            "[green]✓[/green] Standard v2.0 catalog disabled — only imported endpoints are visible."
        )
    else:
        console.print(
            f"[green]✓[/green] Standard v2.0 APIs ready ({state.registry.standard_count} entities)"
        )

    if import_file:
        if not import_file.is_file():
            console.print(f"[red]Import file not found:[/red] {import_file}")
            raise typer.Exit(1)
        _import_endpoints_for_profile(profile_name, import_file)

    console.print("\n[dim]Next:[/dim]")
    if scoped:
        console.print(f"[dim]  bcli auth login --profile {profile_name}[/dim]")
        console.print(f"[dim]  bcli endpoint list --profile {profile_name}[/dim]")
    else:
        console.print("[dim]  bcli get customers --top 5[/dim]")


async def _discover_via_auth(
    *,
    tenant_id: str,
    client_id: str,
    environment: str,
    secret_env: str | None,
    use_device_code: bool,
) -> list[dict]:
    """Authenticate (device code or client creds) and list companies."""
    if use_device_code:
        from bcli.auth._device_code import DeviceCodeAuth

        auth = DeviceCodeAuth(tenant_id=tenant_id, client_id=client_id)
    else:
        auth = ClientCredentialsAuth(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret_env=secret_env,
        )
    transport = BCTransport(auth)
    try:
        return await _discover_companies(transport, environment)
    finally:
        await transport.close()


def _import_endpoints_for_profile(profile_name: str, import_file: Path) -> None:
    """Run a Postman or JSON import for a freshly-created profile.

    Detects the format by inspecting the JSON: Postman v2.1 collections have
    a top-level ``info`` object plus an ``item`` array, while bcli/bcmcp
    registry files have ``endpoints`` or per-group arrays.
    """
    import json

    from bcli.registry._importers import (
        import_from_json,
        import_from_postman,
        save_custom_registry,
    )

    try:
        raw = json.loads(import_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        console.print(f"[red]Could not parse {import_file}:[/red] {e}")
        raise typer.Exit(1) from e

    is_postman = isinstance(raw, dict) and "info" in raw and "item" in raw
    if is_postman:
        endpoints = import_from_postman(import_file)
        source = "postman"
    else:
        endpoints = import_from_json(import_file)
        source = "json"

    if not endpoints:
        console.print(f"[yellow]No endpoints found in {import_file}.[/yellow]")
        return

    path = save_custom_registry(profile_name, endpoints, source=source)
    console.print(
        f"[green]✓[/green] Imported {len(endpoints)} endpoint(s) "
        f"from {import_file.name} → {path}"
    )


@app.command()
def show() -> None:
    """Print current resolved configuration (secrets redacted)."""
    if not CONFIG_FILE.is_file():
        console.print("[yellow]No config found. Run 'bcli config init' first.[/yellow]")
        raise typer.Exit(1)

    config = state.config
    console.print(f"[bold]Config file:[/bold] {CONFIG_FILE}")
    console.print(f"[bold]Default profile:[/bold] {config.defaults.profile}")
    console.print(f"[bold]Output format:[/bold] {config.defaults.format}")
    console.print(f"[bold]Page size:[/bold] {config.defaults.page_size}")
    console.print(f"[bold]Timeout:[/bold] {config.defaults.timeout}s")
    console.print()

    for name, profile in config.profiles.items():
        console.print(f"[bold cyan]Profile: {name}[/bold cyan]")
        console.print(f"  tenant_id: {profile.tenant_id}")
        console.print(f"  environment: {profile.environment}")
        console.print(f"  company_id: {profile.company_id or '(not set)'}")
        console.print(f"  company_name: {profile.company_name or '(not set)'}")
        console.print(f"  auth_method: {profile.auth_method}")
        console.print(f"  client_id: {profile.client_id or '(not set)'}")
        console.print(f"  client_secret_env: {profile.client_secret_env or '(not set)'}")
        if profile.api_publisher:
            console.print(f"  custom API: {profile.api_publisher}/{profile.api_group}/{profile.api_version}")
        console.print()


@app.command("set")
def set_value(key: str, value: str) -> None:
    """Set a config value (e.g., bcli config set defaults.format json)."""
    config = state.config
    parts = key.split(".")

    if len(parts) == 2 and parts[0] == "defaults":
        setattr(config.defaults, parts[1], value)
    elif len(parts) == 3 and parts[0] == "profiles":
        profile_name = parts[1]
        if profile_name not in config.profiles:
            config.profiles[profile_name] = BCProfile(tenant_id="", environment="")
        setattr(config.profiles[profile_name], parts[2], value)
    else:
        console.print(f"[red]Unknown config key: {key}[/red]")
        console.print("[dim]Format: defaults.<key> or profiles.<name>.<key>[/dim]")
        raise typer.Exit(1)

    save_config(config)
    console.print(f"[green]✓[/green] Set {key} = {value}")


@app.command("use")
def use_profile(
    name: str = typer.Argument(help="Profile name to set as default"),
) -> None:
    """Switch the active profile."""
    config = state.config

    if name not in config.profiles:
        available = ", ".join(config.profiles.keys()) or "(none)"
        console.print(f"[red]Profile '{name}' not found. Available: {available}[/red]")
        raise typer.Exit(1)

    config.defaults.profile = name
    save_config(config)

    profile = config.profiles[name]
    console.print(f"[green]✓[/green] Switched to profile '{name}'")
    console.print(f"  Environment: {profile.environment}")
    console.print(f"  Company: {profile.company_name or profile.company_id or '(not set)'}")


@app.command()
def path() -> None:
    """Print the path to the config file."""
    typer.echo(str(CONFIG_FILE))


@app.command()
def edit() -> None:
    """Open the config file in $EDITOR and re-validate on save."""
    if not CONFIG_FILE.is_file():
        console.print("[yellow]No config found. Run 'bcli config init' first.[/yellow]")
        raise typer.Exit(1)

    editor = os.environ.get("EDITOR", "vi")
    try:
        subprocess.run([editor, str(CONFIG_FILE)], check=True)
    except FileNotFoundError as e:
        console.print(f"[red]Editor '{editor}' not found.[/red] Set $EDITOR to a valid command.")
        raise typer.Exit(1) from e
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Editor exited with status {e.returncode}[/red]")
        raise typer.Exit(e.returncode) from e

    try:
        load_config()
    except Exception as e:
        console.print(f"[red]Config is now invalid:[/red] {e}")
        console.print(f"[dim]Fix with 'bcli config edit' or revert {CONFIG_FILE}[/dim]")
        raise typer.Exit(1) from e

    state._config = None
    console.print("[green]✓[/green] Config saved and valid")


async def _discover_companies(transport: BCTransport, environment: str) -> list[dict]:
    url = build_companies_url(environment=environment)
    data = await transport.get(url)
    return data.get("value", [])
