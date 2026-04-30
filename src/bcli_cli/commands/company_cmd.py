"""bcli company — company discovery, selection, and aliasing."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bcli.config import save_config
from bcli.config._model import CompanyAlias
from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("list")
def list_companies(
    format: Optional[str] = typer.Option(
        None, "--format", "-f",
        help="Output format: table (default), json, markdown, csv, ndjson",
    ),
) -> None:
    """List all companies in the current environment.

    JSON shape: ``[{"id": str, "name": str, "alias": str | null, "is_default": bool}]``.
    Stable contract — consumed by the bcli-mcp server's ``list_companies`` tool.
    """
    output_format = format or state.format
    if output_format in ("json", "csv", "ndjson", "markdown", "md"):
        state.quiet = True

    print_context_banner()

    try:
        companies = asyncio.run(_fetch_companies())
        profile = state.profile
        alias_map = {c.id: alias for alias, c in profile.companies.items()}

        # Structured rows used by every format. Keep field names stable —
        # bcli-mcp's list_companies tool consumes this contract.
        rows = [
            {
                "id": company.get("id", ""),
                "name": company.get("name", ""),
                "alias": alias_map.get(company.get("id", ""), "") or None,
                "is_default": (
                    bool(profile.company_id)
                    and company.get("id", "") == profile.company_id
                ),
            }
            for company in companies
        ]

        if output_format and output_format != "table":
            format_output(rows, output_format)
            return

        # Default Rich-table presentation
        table = Table(show_header=True, header_style="bold")
        table.add_column("#", style="dim")
        table.add_column("Alias", style="cyan")
        table.add_column("Company Name")
        table.add_column("Company ID")

        for i, row in enumerate(rows, 1):
            name = row["name"]
            alias = row["alias"] or ""
            if row["is_default"]:
                name = f"[bold green]{name} ◄[/bold green]"
                if not alias:
                    alias = "[dim]default[/dim]"
            table.add_row(str(i), alias, name, row["id"])

        console.print(table)
        console.print(f"[dim]{len(rows)} company(ies)[/dim]")

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


@app.command("aliases-import")
def import_aliases(
    from_profile: str = typer.Option(
        ..., "--from", help="Source profile to copy aliases from"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Replace aliases with the same key in the target profile"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the plan without writing to the config file"
    ),
) -> None:
    """Copy company aliases from another profile into the active profile."""
    config = state.config
    target_name = state.active_profile_name

    if from_profile == target_name:
        console.print(
            f"[red]Source and target are the same profile ('{target_name}'). "
            "Use --profile to select a different target.[/red]"
        )
        raise typer.Exit(1)

    if from_profile not in config.profiles:
        available = ", ".join(config.profiles.keys()) or "(none)"
        console.print(
            f"[red]Source profile '{from_profile}' not found. Available: {available}[/red]"
        )
        raise typer.Exit(1)

    if target_name not in config.profiles:
        console.print(f"[red]Target profile '{target_name}' not found.[/red]")
        raise typer.Exit(1)

    source = config.profiles[from_profile]
    target = config.profiles[target_name]

    if not source.companies:
        console.print(
            f"[yellow]Source profile '{from_profile}' has no aliases to copy.[/yellow]"
        )
        return

    copied: list[str] = []
    overwritten: list[str] = []
    skipped: list[str] = []

    for key, alias in source.companies.items():
        if key in target.companies:
            if overwrite:
                target.companies[key] = alias.model_copy()
                overwritten.append(key)
            else:
                skipped.append(key)
        else:
            target.companies[key] = alias.model_copy()
            copied.append(key)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Alias", style="cyan")
    table.add_column("Status")
    table.add_column("Company ID")
    for key in copied:
        table.add_row(key, "[green]copied[/green]", source.companies[key].id)
    for key in overwritten:
        table.add_row(key, "[yellow]overwritten[/yellow]", source.companies[key].id)
    for key in skipped:
        table.add_row(key, "[dim]skipped (use --overwrite)[/dim]", target.companies[key].id)
    console.print(table)

    written = len(copied) + len(overwritten)
    if dry_run:
        console.print(
            f"[dim]--dry-run: would write {written} alias(es) to '{target_name}', "
            f"skipped {len(skipped)}. Config unchanged.[/dim]"
        )
        return

    if written == 0:
        console.print(
            f"[dim]Nothing to write — all {len(skipped)} alias(es) already exist "
            f"on '{target_name}'. Re-run with --overwrite to replace them.[/dim]"
        )
        return

    save_config(config)
    console.print(
        f"[green]✓[/green] Wrote {written} alias(es) to '{target_name}' "
        f"(copied: {len(copied)}, overwritten: {len(overwritten)}, skipped: {len(skipped)})"
    )


async def _fetch_companies() -> list[dict]:
    async with state.make_async_client() as client:
        return await client.list_companies()
