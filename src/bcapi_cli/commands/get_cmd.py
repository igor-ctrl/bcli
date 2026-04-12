"""bcapi get — query Business Central entities."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

from bcapi.client._async import AsyncBCClient
from bcapi.odata._query import Query
from bcapi_cli._state import state
from bcapi_cli.output import format_output, print_context_banner

console = Console(stderr=True)


def get_command(
    endpoint: str = typer.Argument(help="Entity set name (e.g., 'customers', 'engineOverviews')"),
    record_id: Optional[str] = typer.Argument(None, help="Record ID for single-record GET"),
    filter: Optional[str] = typer.Option(None, "--filter", help="OData $filter expression"),
    select: Optional[str] = typer.Option(None, "--select", help="Comma-separated field names"),
    expand: Optional[str] = typer.Option(None, "--expand", help="Comma-separated navigation properties"),
    orderby: Optional[str] = typer.Option(None, "--orderby", help="OData $orderby expression"),
    top: Optional[int] = typer.Option(None, "--top", help="Max records to return"),
    skip: Optional[int] = typer.Option(None, "--skip", help="Records to skip"),
    count: bool = typer.Option(False, "--count", help="Include total record count"),
    all_pages: bool = typer.Option(False, "--all", help="Follow pagination to get all records"),
    publisher: Optional[str] = typer.Option(None, "--publisher", help="Custom API publisher override"),
    group: Optional[str] = typer.Option(None, "--group", help="Custom API group override"),
    version: Optional[str] = typer.Option(None, "--version", help="Custom API version override"),
) -> None:
    """GET records from a Business Central entity."""
    print_context_banner()

    query = Query()
    if filter:
        query.filter(filter)
    if select:
        query.select(*[s.strip() for s in select.split(",")])
    if expand:
        query.expand(*[e.strip() for e in expand.split(",")])
    if orderby:
        query.orderby(orderby)
    if top:
        query.top(top)
    if skip:
        query.skip(skip)
    if count:
        query.count()

    if state.verbose:
        # Show what we're about to do
        meta = state.registry.get(endpoint)
        if meta:
            console.print(f"[dim]Endpoint: {endpoint} ({meta.route_display})[/dim]")
        params = query.to_params()
        if params:
            console.print(f"[dim]OData: {params}[/dim]")

    if state.dry_run:
        console.print("[yellow]--dry-run: would execute GET, skipping.[/yellow]")
        raise typer.Exit()

    try:
        records = asyncio.run(
            _execute_get(
                endpoint, record_id, query, all_pages,
                publisher=publisher, group=group, version=version,
            )
        )
        format_output(records, state.format)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


async def _execute_get(
    endpoint: str,
    record_id: str | None,
    query: Query,
    all_pages: bool,
    *,
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
) -> list[dict]:
    async with AsyncBCClient(
        profile=state.profile_name,
        config=state.config,
    ) as client:
        if all_pages:
            # Follow pagination
            all_records: list[dict] = []
            bound = client.query(endpoint)
            if publisher and group and version:
                bound.route(publisher, group, version)
            # Apply query params
            if not query.is_empty:
                for f in query._params.filters:
                    bound.filter(f)
                for s in query._params.selects:
                    bound.select(s)
                for e in query._params.expands:
                    bound.expand(e)
                if query._params.orderby:
                    bound.orderby(query._params.orderby)

            pages = await bound.pages()
            async for page in pages:
                all_records.extend(page)
            return all_records
        else:
            response = await client.get(
                endpoint, record_id,
                query=query,
                publisher=publisher,
                group=group,
                version=version,
            )
            if record_id:
                # Single record — wrap in list for formatter
                return [response.raw] if response.raw else []
            return response.value
