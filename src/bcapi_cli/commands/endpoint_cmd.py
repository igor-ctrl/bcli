"""bcapi endpoint — endpoint discovery."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bcapi_cli._state import state

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("list")
def list_endpoints(
    custom: bool = typer.Option(False, "--custom", help="Show only custom (imported) endpoints"),
    standard: bool = typer.Option(False, "--standard", help="Show only standard v2.0 endpoints"),
    category: Optional[str] = typer.Option(None, "--category", help="Filter by category"),
) -> None:
    """List all known endpoints (standard + custom)."""
    registry = state.registry
    endpoints = registry.list_all(custom_only=custom, standard_only=standard)

    if category:
        endpoints = [e for e in endpoints if e.category.lower() == category.lower()]

    table = Table(show_header=True, header_style="bold")
    table.add_column("Entity", style="cyan")
    table.add_column("Route")
    table.add_column("Operations")
    table.add_column("Category", style="dim")
    table.add_column("Description", max_width=50)

    for ep in endpoints:
        ops = ", ".join(ep.supports)
        table.add_row(
            ep.entity_set_name,
            ep.route_display,
            ops,
            ep.category,
            ep.description[:50] if ep.description else "",
        )

    console.print(table)
    console.print(
        f"[dim]{len(endpoints)} endpoint(s)"
        f" ({registry.standard_count} standard, {registry.custom_count} custom)[/dim]"
    )


@app.command("search")
def search_endpoints(
    query: str = typer.Argument(help="Search term"),
) -> None:
    """Fuzzy search endpoints by name or description."""
    results = state.registry.search(query)

    if not results:
        console.print(f"[yellow]No endpoints matching '{query}'[/yellow]")
        raise typer.Exit()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Entity", style="cyan")
    table.add_column("Route")
    table.add_column("Description", max_width=60)

    for ep in results[:20]:
        table.add_row(ep.entity_set_name, ep.route_display, ep.description)

    console.print(table)


@app.command("info")
def endpoint_info(
    name: str = typer.Argument(help="Entity set name"),
) -> None:
    """Show detailed metadata for an endpoint."""
    ep = state.registry.get(name)
    if not ep:
        console.print(f"[red]Endpoint '{name}' not found.[/red]")
        suggestions = state.registry.search(name)[:3]
        if suggestions:
            console.print(f"[dim]Did you mean: {', '.join(s.entity_set_name for s in suggestions)}?[/dim]")
        raise typer.Exit(1)

    console.print(f"[bold]{ep.entity_set_name}[/bold]")
    console.print(f"  Entity name:  {ep.entity_name}")
    console.print(f"  Route:        {ep.route_display}")
    console.print(f"  Operations:   {', '.join(ep.supports)}")
    console.print(f"  Key field:    {ep.key_field}")
    console.print(f"  Category:     {ep.category}")
    console.print(f"  Custom:       {'Yes' if ep.is_custom else 'No (standard v2.0)'}")
    if ep.description:
        console.print(f"  Description:  {ep.description}")
    if ep.source_table:
        console.print(f"  Source table: {ep.source_table}")
    if ep.page_number:
        console.print(f"  Page number:  {ep.page_number}")
