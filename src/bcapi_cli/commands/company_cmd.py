"""bcapi company — company discovery and selection."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bcapi.client._async import AsyncBCClient
from bcapi.config import save_config
from bcapi_cli._state import state
from bcapi_cli.output import print_context_banner

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("list")
def list_companies() -> None:
    """List all companies in the current environment."""
    print_context_banner()

    try:
        companies = asyncio.run(_fetch_companies())

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", style="dim")
        table.add_column("Company Name")
        table.add_column("Company ID")

        for i, company in enumerate(companies, 1):
            name = company.get("name", "")
            cid = company.get("id", "")
            # Highlight the current company
            current = state.profile.company_id
            if current and cid == current:
                name = f"[bold green]{name} ◄[/bold green]"
            table.add_row(str(i), name, cid)

        console.print(table)
        console.print(f"[dim]{len(companies)} company(ies)[/dim]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("use")
def use_company(
    company_id: str = typer.Argument(help="Company ID (GUID) or number from 'company list'"),
) -> None:
    """Set the default company for the active profile."""
    config = state.config
    profile_name = state.active_profile_name

    if profile_name not in config.profiles:
        console.print(f"[red]Profile '{profile_name}' not found.[/red]")
        raise typer.Exit(1)

    config.profiles[profile_name].company_id = company_id
    save_config(config)
    console.print(f"[green]✓[/green] Set company to {company_id} for profile '{profile_name}'")


async def _fetch_companies() -> list[dict]:
    async with AsyncBCClient(profile=state.profile_name, config=state.config) as client:
        return await client.list_companies()
