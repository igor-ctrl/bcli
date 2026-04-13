"""bcli env — environment discovery and selection."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from bcli.client._async import AsyncBCClient
from bcli.config import save_config
from bcli_cli._state import state
from bcli_cli.output import print_context_banner

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("list")
def list_environments() -> None:
    """List available Business Central environments."""
    print_context_banner()

    try:
        envs = asyncio.run(_fetch_environments())

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", style="dim")
        table.add_column("Name")
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Version")
        table.add_column("Country")

        current_env = state.profile.environment

        for i, env in enumerate(envs, 1):
            name = env.get("name", "")
            display_name = name
            if name == current_env:
                display_name = f"[bold green]{name} ◄[/bold green]"
            table.add_row(
                str(i),
                display_name,
                env.get("type", ""),
                env.get("status", ""),
                env.get("applicationVersion", env.get("platformVersion", "")),
                env.get("countryCode", ""),
            )

        console.print(table)
        console.print(f"[dim]{len(envs)} environment(s)[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print(
            "[dim]Note: Environment discovery requires the app registration to have "
            "admin API permissions (D365 Admin Center API).[/dim]"
        )
        raise typer.Exit(1)


@app.command("use")
def use_environment(
    name: str = typer.Argument(help="Environment name (e.g., 'Production', 'Sandbox')"),
) -> None:
    """Set the default environment for the active profile."""
    config = state.config
    profile_name = state.active_profile_name

    if profile_name not in config.profiles:
        console.print(f"[red]Profile '{profile_name}' not found.[/red]")
        raise typer.Exit(1)

    config.profiles[profile_name].environment = name
    # Clear company since it may differ across environments
    config.profiles[profile_name].company_id = None
    config.profiles[profile_name].company_name = None
    save_config(config)
    console.print(f"[green]✓[/green] Set environment to '{name}' for profile '{profile_name}'")
    console.print("[dim]Company cleared — run 'bcli company list' to select one.[/dim]")


async def _fetch_environments() -> list[dict]:
    async with AsyncBCClient(profile=state.profile_name, config=state.config) as client:
        return await client.list_environments()
