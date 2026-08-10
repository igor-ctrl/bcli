"""bcli get — query Business Central entities."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bcli.odata._query import Query
from bcli_cli._out_path import prepare_out_path
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
    out: Optional[Path] = typer.Option(None, "--out", help="Write the record's media stream (raw bytes) to this path instead of printing records"),
    media: Optional[str] = typer.Option(None, "--media", help="Media property to download (default: auto-discover from the record's @odata.mediaReadLink annotations)"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing --out file (refused by default)"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, json, csv, ndjson, raw"),
    publisher: Optional[str] = typer.Option(None, "--publisher", help="Custom API publisher override (escape hatch — registry resolves this automatically)"),
    group: Optional[str] = typer.Option(None, "--group", help="Custom API group override (escape hatch — registry resolves this automatically)"),
    version: Optional[str] = typer.Option(None, "--version", help="Custom API version override (escape hatch — registry resolves this automatically)"),
) -> None:
    """GET records from a Business Central entity.

    \b
    Examples:
      bcli get customers --top 5 -f json
      bcli get vendors --filter "displayName eq 'Fabrikam'"
      bcli get items --filter "unitPrice gt 100" --all
      bcli get salesInvoices --select number,totalAmountIncludingTax --orderby "number desc"
      bcli get incomingDocuments <systemId> --out invoice.pdf
    """
    _validate_out_flags(
        out, media, record_id, endpoint,
        query_flags={
            "--filter": filter, "--select": select, "--expand": expand,
            "--orderby": orderby, "--top": top, "--skip": skip,
            "--count": count, "--all": all_pages,
        },
        # Only a locally-passed --format conflicts. A format inherited from
        # config or a global flag is a preference about *printed records*, and
        # --out prints none — silently ignoring it beats failing a command the
        # user spelled correctly.
        explicit_format=format is not None,
    )

    # Resolve and vet the destination before anything else: a --out run that
    # can't write should cost no round trip.
    dest = prepare_out_path(out, overwrite=overwrite) if out is not None else None

    # Local --format overrides global
    output_format = format or state.format
    explicit_format = (format is not None) or state.format_explicit
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    if dest is not None:
        # record_id is guaranteed non-empty by _validate_out_flags.
        _run_media_download(
            endpoint, record_id or "", dest, media,
            publisher=publisher, group=group, version=version,
            overwrite=overwrite,
        )
        return

    if filter:
        _check_filter_fields(endpoint, filter)

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
            format_output(records, output_format, auto_format=not explicit_format)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
        return

    import time as _time

    from bcli.telemetry import events as _tev

    sink = state.telemetry
    started = _time.monotonic()
    try:
        records = asyncio.run(
            _execute_get(
                endpoint, record_id, query, all_pages,
                publisher=publisher, group=group, version=version,
            )
        )
        format_output(records, output_format)
        latency_ms = (_time.monotonic() - started) * 1000.0
        capture_filter = state.config.telemetry.capture_filter_text
        sink.emit(*_tev.query(
            endpoint=endpoint,
            has_filter=bool(filter),
            select_count=len(select.split(",")) if select else 0,
            top=top if top is not None else -1,
            skip=skip if skip is not None else -1,
            all_pages=all_pages,
            status=200,
            latency_ms=latency_ms,
            filter_text=(filter or "") if capture_filter else "",
        ))
    except Exception as e:
        latency_ms = (_time.monotonic() - started) * 1000.0
        sink.emit(*_tev.error(
            error_class=type(e).__name__,
            http_status=getattr(e, "status_code", 0) or 0,
            bc_message=getattr(e, "bc_message", "") or str(e),
            correlation_id=getattr(e, "correlation_id", "") or "",
            endpoint=endpoint,
        ))
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


def _validate_out_flags(
    out: Optional[Path],
    media: Optional[str],
    record_id: Optional[str],
    endpoint: str,
    *,
    query_flags: dict[str, object],
    explicit_format: bool,
) -> None:
    """Enforce the ``--out`` contract before anything reaches the network.

    ``--out`` switches ``get`` from "print a list of records" to "stream one
    record's media property to a file". Every flag that shapes a record *list*
    is therefore evidence the caller meant the other mode, and answering with
    a file they didn't expect is worse than refusing.
    """
    if out is None:
        if media is not None:
            raise typer.BadParameter(
                "--media requires --out — it names which media property to write, "
                "and without --out there is nowhere to write it."
            )
        return

    if not record_id:
        raise typer.BadParameter(
            f"--out needs a record id: bcli get {endpoint} <record-id> --out <path>. "
            f"Find one first with: bcli get {endpoint} --filter \"...\" --top 1 -f json"
        )

    conflicting = sorted(name for name, value in query_flags.items() if value)
    if conflicting:
        raise typer.BadParameter(
            f"--out streams one record's media bytes, so it cannot be combined with "
            f"{', '.join(conflicting)}. Drop those to download, or drop --out to query."
        )

    if explicit_format:
        raise typer.BadParameter(
            "--out writes raw bytes to a file, so --format has no records to format. "
            "Pass one or the other."
        )


def _run_media_download(
    endpoint: str,
    record_id: str,
    dest: Path,
    media_field: str | None,
    *,
    publisher: str | None,
    group: str | None,
    version: str | None,
    overwrite: bool,
) -> None:
    """Execute the ``--out`` branch: fetch the record, stream its media to ``dest``."""
    if state.dry_run:
        which = media_field or "auto-discovered from @odata.mediaReadLink"
        console.print(
            f"[yellow]--dry-run:[/yellow] would GET {endpoint}({record_id}), read its "
            f"media property ({which}) and write the bytes to {dest}. "
            f"Nothing fetched, nothing written."
        )
        raise typer.Exit()

    import time as _time

    from bcli.telemetry import events as _tev

    sink = state.telemetry
    started = _time.monotonic()
    try:
        result = asyncio.run(
            _execute_get_media(
                endpoint, record_id, dest, media_field,
                publisher=publisher, group=group, version=version,
                overwrite=overwrite,
            )
        )
        latency_ms = (_time.monotonic() - started) * 1000.0
        name, props = _tev.query(
            endpoint=endpoint,
            has_filter=False,
            status=200,
            latency_ms=latency_ms,
        )
        # Additive dimension on the existing query event, so a media download
        # still shows up in the same KQL as any other read but can be told
        # apart from one.
        props["media_download"] = True
        sink.emit(name, props)
        console.print(
            f"[green]✓[/green] Wrote {result['bytes_written']:,} bytes to "
            f"{result['path']} ({result['content_type'] or 'unknown content type'}, "
            f"media field: {result['media_field']})"
        )
    except Exception as e:
        sink.emit(*_tev.error(
            error_class=type(e).__name__,
            http_status=getattr(e, "status_code", 0) or 0,
            bc_message=getattr(e, "bc_message", "") or str(e),
            correlation_id=getattr(e, "correlation_id", "") or "",
            endpoint=endpoint,
        ))
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


async def _execute_get_media(
    endpoint: str,
    record_id: str,
    dest: Path,
    media_field: str | None,
    *,
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
    overwrite: bool = False,
) -> dict:
    async with state.make_async_client() as client:
        return await client.get_media(
            endpoint, record_id, dest,
            media_field=media_field,
            publisher=publisher, group=group, version=version,
            overwrite=overwrite,
        )


def _check_filter_fields(endpoint: str, filter_expr: str) -> None:
    """Pre-flight check: warn if --filter references fields the entity doesn't have.

    No-op for built-in standard endpoints (their field lists aren't catalogued)
    or for custom endpoints whose registry entry hasn't learned its fields yet
    — in those cases BC's own 400 is still the source of truth.
    """
    from bcli.odata._filter_fields import validate_filter_fields

    meta = state.registry.get(endpoint)
    if not meta or not meta.field_names:
        return
    result = validate_filter_fields(filter_expr, meta.field_names)
    if result is None:
        return
    msg, _ = result
    console.print(f"[red]Error:[/red] {msg}")
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
