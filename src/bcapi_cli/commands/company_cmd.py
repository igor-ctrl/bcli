"""bcli company — company discovery, selection, and aliasing."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bcapi.client._async import AsyncBCClient
from bcapi.config import save_config
from bcapi.config._model import CompanyAlias
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
        profile = state.profile

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", style="dim")
        table.add_column("Alias", style="cyan")
        table.add_column("Company Name")
        table.add_column("Company ID")

        # Build reverse lookup: company_id → alias
        alias_map = {c.id: alias for alias, c in profile.companies.items()}

        for i, company in enumerate(companies, 1):
            name = company.get("name", "")
            cid = company.get("id", "")
            alias = alias_map.get(cid, "")

            # Highlight the current default company
            if profile.company_id and cid == profile.company_id:
                name = f"[bold green]{name} ◄[/bold green]"
                if not alias:
                    alias = "[dim]default[/dim]"

            table.add_row(str(i), alias, name, cid)

        console.print(table)
        console.print(f"[dim]{len(companies)} company(ies)[/dim]")

        if not profile.companies:
            console.print(
                "\n[dim]Tip: assign nicknames with 'bcli company alias <name> <company-id>'[/dim]"
            )

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("use")
def use_company(
    company: str = typer.Argument(help="Company ID (GUID) or alias"),
) -> None:
    """Set the default company for the active profile."""
    config = state.config
    profile_name = state.active_profile_name
    profile = config.profiles.get(profile_name)

    if not profile:
        console.print(f"[red]Profile '{profile_name}' not found.[/red]")
        raise typer.Exit(1)

    # Resolve alias if applicable
    if company in profile.companies:
        resolved = profile.companies[company]
        profile.company_id = resolved.id
        profile.company_name = resolved.name or company
        save_config(config)
        console.print(f"[green]✓[/green] Set default company to '{company}' ({resolved.id[:8]}...)")
    else:
        profile.company_id = company
        profile.company_name = None
        save_config(config)
        console.print(f"[green]✓[/green] Set default company to {company}")


@app.command("alias")
def alias_company(
    name: str = typer.Argument(help="Short alias (e.g., 'LLC', 'Corp')"),
    company_id: str = typer.Argument(help="Company ID (GUID)"),
    display_name: Optional[str] = typer.Option(None, "--name", "-n", help="Display name"),
) -> None:
    """Assign a nickname to a company for quick access."""
    config = state.config
    profile_name = state.active_profile_name
    profile = config.profiles.get(profile_name)

    if not profile:
        console.print(f"[red]Profile '{profile_name}' not found.[/red]")
        raise typer.Exit(1)

    profile.companies[name] = CompanyAlias(id=company_id, name=display_name or "")
    save_config(config)
    console.print(f"[green]✓[/green] Alias '{name}' → {company_id[:8]}...")
    console.print(f"[dim]Use: bcli get customers --company {name}[/dim]")


@app.command("aliases")
def list_aliases() -> None:
    """Show all company aliases for the active profile."""
    profile = state.profile

    if not profile.companies:
        console.print("[dim]No aliases configured. Use 'bcli company alias <name> <id>'.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Alias", style="cyan")
    table.add_column("Company Name")
    table.add_column("Company ID")
    table.add_column("Default", style="dim")

    for alias, company in profile.companies.items():
        is_default = "◄" if company.id == profile.company_id else ""
        table.add_row(alias, company.name, company.id, is_default)

    console.print(table)


async def _fetch_companies() -> list[dict]:
    async with AsyncBCClient(profile=state.profile_name, config=state.config) as client:
        return await client.list_companies()
