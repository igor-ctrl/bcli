"""bcli get — query Business Central entities."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

from bcli.odata._query import Query
from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

console = Console(stderr=True)


def get_command(
    endpoint: str = typer.Argument(help="Entity set name (e.g., 'customers', 'vendors')"),
    record_id: Optional[str] = typer.Argument(None, help="Record ID for single-record GET"),
    filter: Optional[str] = typer.Option(None, "--filter", help="OData $filter expression"),
    select: Optional[str] = typer.Option(None, "--select", help="Comma-separated field names"),
    expand: Optional[str] = typer.Option(None, "--expand", help="Comma-separated navigation properties"),
    orderby: Optional[str] = typer.Option(None, "--orderby", help="OData $orderby expression"),
    top: Optional[int] = typer.Option(None, "--top", help="Max records to return"),
    skip: Optional[int] = typer.Option(None, "--skip", help="Records to skip"),
    count: bool = typer.Option(False, "--count", help="Include total record count"),
    all_pages: bool = typer.Option(False, "--all", help="Follow pagination to get all records"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, json, csv, ndjson, raw"),
    publisher: Optional[str] = typer.Option(None, "--publisher", help="Custom API publisher override"),
    group: Optional[str] = typer.Option(None, "--group", help="Custom API group override"),
    version: Optional[str] = typer.Option(None, "--version", help="Custom API version override"),
) -> None:
    """GET records from a Business Central entity.

    \b
    Examples:
      bcli get customers --top 5 -f json
      bcli get vendors --filter "displayName eq 'Fabrikam'"
      bcli get items --filter "unitPrice gt 100" --all
      bcli get salesInvoices --select number,totalAmountIncludingTax --orderby "number desc"
    """
    # Local --format overrides global
    output_format = format or state.format
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

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
    if top is not None:
        query.top(top)
    if skip is not None:
        query.skip(skip)
    if count:
        query.count()

    if state.verbose:
        meta = state.registry.get(endpoint)
        if meta:
            console.print(f"[dim]Endpoint: {endpoint} ({meta.route_display})[/dim]")
        params = query.to_params()
        if params:
            console.print(f"[dim]OData: {params}[/dim]")

    if state.dry_run:
        console.print("[yellow]--dry-run: would execute GET, skipping.[/yellow]")
        raise typer.Exit()

    # Check if --company all was passed (via global flag)
    company_override = state.company_override
    if company_override and company_override.lower() == "all":
        try:
            records = asyncio.run(
                _execute_get_all_companies(
                    endpoint, query, top,
                    publisher=publisher, group=group, version=version,
                )
            )
            format_output(records, output_format)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
        return

    try:
        records = asyncio.run(
            _execute_get(
                endpoint, record_id, query, all_pages,
                publisher=publisher, group=group, version=version,
            )
        )
        format_output(records, output_format)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


async def _execute_get_all_companies(
    endpoint: str,
    query: Query,
    top: int | None,
    *,
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
) -> list[dict]:
    """Query across all company aliases, tagging each record with _company."""
    profile = state.config.get_profile(state.profile_name)
    companies = profile.all_companies()

    if not companies:
        console.print("[yellow]No company aliases configured. Use 'bcli company alias' first.[/yellow]")
        return []

    all_records: list[dict] = []

    async with state.make_async_client() as client:
        for alias, company_id, company_name in companies:
            display = alias or company_name or company_id[:8]
            console.print(f"[dim]Querying {endpoint} in {display}...[/dim]")

            # Override the company for this query
            url = client._resolve_url(
                endpoint,
                publisher=publisher,
                group=group,
                version=version,
            )
            # Replace the company_id in the URL
            from bcli._url import build_url
            ep = client.registry.get(endpoint)
            if ep and ep.is_custom:
                url = build_url(
                    environment=profile.environment,
                    company_id=company_id,
                    entity_set_name=endpoint,
                    publisher=ep.api_publisher,
                    group=ep.api_group,
                    version=ep.api_version,
                )
            else:
                url = build_url(
                    environment=profile.environment,
                    company_id=company_id,
                    entity_set_name=endpoint,
                    publisher=publisher,
                    group=group,
                    version=version,
                )

            transport = client._ensure_transport()
            params = query.to_params()
            data = await transport.get(url, params=params)
            records = data.get("value", [])

            # Tag each record with _company
            for record in records:
                record["_company"] = alias
                record["_company_name"] = company_name

            all_records.extend(records)
            console.print(f"[dim]  → {len(records)} record(s) from {display}[/dim]")

    return all_records


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
    async with state.make_async_client() as client:
        if all_pages:
            all_records: list[dict] = []
            bound = client.query(endpoint)
            if publisher and group and version:
                bound.route(publisher, group, version)
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
                return [response.raw] if response.raw else []
            return response.value
