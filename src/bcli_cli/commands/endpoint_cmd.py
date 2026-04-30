"""bcli endpoint — endpoint discovery."""

from __future__ import annotations

import asyncio
import json as _json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bcli_cli._state import state
from bcli_cli.output import format_output

app = typer.Typer(no_args_is_help=True)
console = Console()
stderr_console = Console(stderr=True)


def _endpoint_to_dict(ep) -> dict:
    """Stable JSON shape for an endpoint — consumed by bcli-mcp."""
    return {
        "name": ep.entity_set_name,
        "category": ep.category,
        "custom": ep.is_custom,
        "supported_ops": list(ep.supports),
        "key_field": ep.key_field,
        "publisher": ep.api_publisher,
        "group": ep.api_group,
        "version": ep.api_version,
        "description": ep.description or "",
    }


@app.command("list")
def list_endpoints(
    custom: bool = typer.Option(False, "--custom", help="Show only custom (imported) endpoints"),
    standard: bool = typer.Option(False, "--standard", help="Show only standard v2.0 endpoints"),
    category: Optional[str] = typer.Option(None, "--category", help="Filter by category"),
    format: Optional[str] = typer.Option(
        None, "--format", "-f",
        help="Output format: table (default), json, markdown, csv, ndjson",
    ),
) -> None:
    """List all known endpoints (standard + custom).

    JSON shape: ``[{"name": str, "category": str, "custom": bool,
    "supported_ops": [str], "key_field": str, "publisher": str|null,
    "group": str|null, "version": str|null, "description": str}]``.
    """
    registry = state.registry
    endpoints = registry.list_all(custom_only=custom, standard_only=standard)

    if category:
        endpoints = [e for e in endpoints if e.category.lower() == category.lower()]

    output_format = format or state.format

    if output_format and output_format != "table":
        rows = [_endpoint_to_dict(ep) for ep in endpoints]
        format_output(rows, output_format)
        return

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
    format: Optional[str] = typer.Option(
        None, "--format", "-f",
        help="Output format: text (default) or json",
    ),
) -> None:
    """Show detailed metadata for an endpoint.

    JSON shape extends ``endpoint list`` with ``entity_name``, ``fields``
    (from prior ``bcli endpoint fields`` runs), ``source_table``, and
    ``page_number``.
    """
    ep = state.registry.get(name)
    if not ep:
        stderr_console.print(f"[red]Endpoint '{name}' not found.[/red]")
        suggestions = state.registry.search(name)[:3]
        if suggestions:
            stderr_console.print(
                f"[dim]Did you mean: {', '.join(s.entity_set_name for s in suggestions)}?[/dim]"
            )
        raise typer.Exit(1)

    output_format = format or state.format

    if output_format == "json":
        payload = _endpoint_to_dict(ep)
        payload["entity_name"] = ep.entity_name
        payload["fields"] = [{"name": f, "type": ""} for f in ep.field_names]
        # Hint for downstream consumers (esp. bcli-mcp) so they can tell the
        # difference between "no fields exist" and "fields not yet cached".
        # Empty fields with fields_discovered=False means: run
        # `bcli endpoint fields <name>` to populate, or probe via a query.
        payload["fields_discovered"] = bool(ep.field_names)
        payload["source_table"] = ep.source_table
        payload["page_number"] = ep.page_number
        print(_json.dumps(payload, indent=2, default=str))
        return

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
    if ep.field_names:
        console.print(f"  Fields:       {', '.join(ep.field_names)}")


@app.command("fields")
def endpoint_fields(
    name: str = typer.Argument(help="Entity set name"),
) -> None:
    """Discover field names and types by fetching one record from the API."""
    ep = state.registry.get(name)
    if not ep:
        stderr_console.print(f"[red]Endpoint '{name}' not found.[/red]")
        suggestions = state.registry.search(name)[:3]
        if suggestions:
            stderr_console.print(
                f"[dim]Did you mean: {', '.join(s.entity_set_name for s in suggestions)}?[/dim]"
            )
        raise typer.Exit(1)

    stderr_console.print(f"[dim]Fetching one record from '{name}'...[/dim]")

    try:
        record = asyncio.run(_fetch_one_record(name))
    except Exception as e:
        stderr_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not record:
        stderr_console.print(
            f"[yellow]No data returned for '{name}'.[/yellow]\n"
            f"[dim]The endpoint exists but has no records, or requires filters.[/dim]"
        )
        raise typer.Exit()

    route = ep.route_display
    print(f"Fields for '{name}' ({route}):")
    field_names: list[str] = []
    for key, value in record.items():
        if key.startswith("@odata"):
            continue
        field_names.append(key)
        type_name = _infer_type(value)
        sample = _format_sample(value)
        print(f"  {key:<30} {type_name:<10} {sample}")

    # Persist discovered fields onto the custom registry entry so subsequent
    # --filter validation can suggest the right field name when the user
    # mistypes one. No-op for built-in standard endpoints.
    if ep.is_custom and field_names:
        from bcli.registry._importers import update_endpoint_fields

        if update_endpoint_fields(state.active_profile_name, name, field_names):
            stderr_console.print(
                f"[dim]Saved {len(field_names)} field name(s) to the registry "
                f"for filter-validation suggestions.[/dim]"
            )


async def _fetch_one_record(entity_set_name: str) -> dict | None:
    async with state.make_async_client() as client:
        response = await client.get(entity_set_name, query=None)
        records = response.value
        return records[0] if records else None


def _infer_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        if len(value) == 36 and value.count("-") == 4:
            return "guid"
        if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
            return "date"
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "unknown"


def _format_sample(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        truncated = value[:40] + "..." if len(value) > 40 else value
        return f'"{truncated}"'
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
