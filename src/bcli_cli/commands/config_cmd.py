"""bcli config — configuration management commands."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from bcli._url import build_companies_url
from bcli.auth._credentials import ClientCredentialsAuth
from bcli.client._transport import BCTransport
from bcli.config import BCConfig, BCDefaults, BCProfile, load_config, save_config
from bcli.config._defaults import CONFIG_FILE
from bcli_cli._state import state

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def init() -> None:
    """Interactive setup wizard — configure a profile and discover companies."""
    console.print("[bold]bcli config init[/bold]\n")

    profile_name = Prompt.ask("Profile name", default="default")
    tenant_id = Prompt.ask("Tenant ID (Azure AD)")
    environment = Prompt.ask("Environment name", default="Production")
    client_id = Prompt.ask("Client ID (App Registration)")

    # Secret handling — offer keychain first
    from bcli.auth._credentials import ClientCredentialsAuth

    secret_env = None
    if ClientCredentialsAuth.has_keyring():
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
    profile = BCProfile(
        tenant_id=tenant_id,
        environment=environment,
        auth_method="client_credentials",
        client_id=client_id,
        client_secret_env=secret_env,
    )

    # Try to authenticate and discover companies
    console.print("\n[dim]Authenticating...[/dim]")
    try:
        async def _discover_and_close():
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

        companies = asyncio.run(_discover_and_close())

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
            profile.company_id = selected.get("id", "")
            profile.company_name = selected.get("name", "")
            console.print(f"[green]✓[/green] Selected: {profile.company_name}")
        else:
            console.print("[yellow]No companies found. You can set company_id manually later.[/yellow]")

    except Exception as e:
        console.print(f"[yellow]⚠ Could not connect: {e}[/yellow]")
        console.print("[dim]Saving config anyway — you can test the connection later.[/dim]")

    # Save config — load existing to preserve other profiles
    config = load_config()
    config.defaults.profile = profile_name
    config.profiles[profile_name] = profile
    path = save_config(config)
    state.config = config

    console.print(f"\n[green]✓[/green] Config saved to {path}")
    console.print(f"[green]✓[/green] Standard v2.0 APIs ready ({state.registry.standard_count} entities)")
    console.print(f"\n[dim]Try: bcli get customers --top 5[/dim]")


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


async def _discover_companies(transport: BCTransport, environment: str) -> list[dict]:
    url = build_companies_url(environment=environment)
    data = await transport.get(url)
    return data.get("value", [])
