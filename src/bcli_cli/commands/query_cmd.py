"""bcli q — saved-query aliases for the daily questions a domain user asks.

Hides OData syntax behind named, parametrised queries stored per-profile in
``~/.config/bcli/queries/{profile}.yaml``. A non-developer user can run
``bcli q customer-by-name name=Fabrikam`` instead of remembering that the
filter field is ``displayName`` and the operator is ``eq``.

YAML schema (per file)::

    queries:
      <name>:
        description: "human description"
        # — discoverability metadata (all optional, all additive) —
        aliases:   [synonym1, synonym2]
        tags:      [period-close, ap, intercompany]
        owner:     finance-ops
        freshness: live          # live | daily | reference
        examples:
          - bcli q <name> name=Fabrikam
        related:   [other-query-name]
        # — query body —
        endpoint: <entitySetName>
        params:
          <key>:
            required: true
            default: <value>
            type: string | integer | number | boolean   # default: string
            pattern: "^[A-Z0-9]{2,8}$"                  # string-only regex
            min: 1                                       # numeric lower bound
            max: 1000                                    # numeric upper bound
            enum: ["Open", "Posted"]                    # allowed literal set
            hint: "BC Vendor No."                       # shown in `bcli q info`
        filter:  "displayName eq '${{ params.name }}'"
        select:  "number,displayName,email,phoneNumber"
        orderby: "displayName asc"
        top: 50
        all: false

The metadata fields exist purely for human discoverability — ``bcli q list``
filters by tag/owner, ``bcli q search`` ranks across name/alias/tag/desc,
and ``bcli q info`` shows the full record. None of the metadata changes
how a query executes.

This module is a thin CLI shell over :mod:`bcli.queries`, which owns the
actual catalog loading, parameter validation, and ``${{ }}`` expansion
logic (so a non-CLI consumer — a workflow step, a remote MCP server — can
reuse it without importing anything under ``bcli_cli``). What stays here is
CLI-specific: parsing ``key=value`` argv into typed values, console
formatting, and exit codes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from bcli.config._defaults import CONFIG_DIR
from bcli.queries import (
    ODATA_FIELDS,
    QueryCatalogError,
    QueryEntry,
    QueryParamError,
    ResolvedQuery,
    expand_query,
    filter_entries,
    load_catalog,
    normalize_queries,
    resolve_query_name,
    search_entries,
)
from bcli.queries import resolve_params as _sdk_resolve_params
from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

console = Console(stderr=True)

QUERIES_DIR = CONFIG_DIR / "queries"


def query_command(
    name: Optional[str] = typer.Argument(
        None,
        help="Saved query name. Omit to list available queries.",
    ),
    params: Optional[list[str]] = typer.Argument(
        None,
        help="Parameter assignments as key=value (repeatable).",
    ),
    show: bool = typer.Option(
        False, "--show",
        help="Print the resolved OData query without executing it.",
    ),
    format: Optional[str] = typer.Option(
        None, "--format", "-f",
        help="Output format override.",
    ),
) -> None:
    """Run a saved query (no OData required).

    \b
    Examples:
      bcli q                                  # list saved queries for this profile
      bcli q customer-by-name name=Fabrikam
      bcli q open-invoices-by-customer customer-id=C00010 limit=20
      bcli q customer-by-name name=Fabrikam --show
      bcli q search "overdue invoices"        # discover by NL phrase
      bcli q info customer-by-name             # full metadata for one query
    """
    profile_name = state.active_profile_name
    queries_file = QUERIES_DIR / f"{profile_name}.yaml"

    if name is None:
        _list_queries(profile_name, queries_file)
        return

    # Sub-verb dispatch.
    #
    # The first positional arg can be a real query name, a reserved
    # sub-verb (`list`, `search`, `find`, `info`), or `run` — the
    # explicit escape hatch for cases where someone has authored a
    # query whose name shadows a sub-verb. Reserved names produce a
    # hard error at bundle-load time (see `bcli.queries.load_catalog`,
    # which checks `RESERVED_QUERY_NAMES`) so this branch is never
    # reached for a well-formed bundle, but `bcli q run <name>` ensures
    # users always have a way to invoke a hypothetically-misnamed query
    # without editing the bundle.
    if name == "run":
        if not params:
            console.print("[red]`bcli q run <name> [key=value …]` expected.[/red]")
            raise typer.Exit(2)
        # Re-enter with name=<first param>, params=<rest> — keeps the
        # validation path identical to the regular invocation.
        run_name, *run_params = params
        return query_command(
            name=run_name,
            params=run_params or None,
            show=show,
            format=format,
        )
    if name in ("search", "find"):
        if not params:
            console.print("[red]`bcli q search <phrase>` expected.[/red]")
            raise typer.Exit(2)
        _search_queries(profile_name, queries_file, " ".join(params))
        return
    if name == "info":
        if not params:
            console.print("[red]`bcli q info <query-name>` expected.[/red]")
            raise typer.Exit(2)
        _query_info(profile_name, queries_file, params[0])
        return
    if name == "list":
        # Optional filters live in `params` as `tag=foo owner=bar` pairs.
        flt = {k: v for k, _, v in (p.partition("=") for p in (params or []))}
        _list_queries(
            profile_name,
            queries_file,
            tag=flt.get("tag"),
            owner=flt.get("owner"),
            freshness=flt.get("freshness"),
        )
        return

    saved = _load_saved_queries(queries_file)
    canonical = resolve_query_name(saved, name)
    if canonical is None:
        available = ", ".join(sorted(saved.keys())) or "(none)"
        console.print(
            f"[red]Saved query '{name}' not found.[/red] Available: {available}\n"
            f"[dim]Edit {queries_file} to add one,"
            f" or run `bcli q search '{name}'` to find a near match.[/dim]"
        )
        raise typer.Exit(1)
    name = canonical

    spec = saved[name]
    resolved_params = _resolve_params(spec.get("params", {}), params or [])
    resolved = expand_query(spec, resolved_params)

    endpoint = resolved.endpoint
    if not endpoint:
        console.print(f"[red]Saved query '{name}' has no 'endpoint'.[/red]")
        raise typer.Exit(1)

    if show:
        _print_resolved(name, endpoint, resolved)
        return

    output_format = format or state.format
    explicit_format = (format is not None) or state.format_explicit
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    if state.dry_run:
        _print_resolved(name, endpoint, resolved)
        console.print("[yellow]--dry-run: would execute, skipping.[/yellow]")
        return

    import time as _time

    from bcli.telemetry import events as _tev

    sink = state.telemetry
    started = _time.monotonic()
    try:
        records = asyncio.run(_run_saved_query(endpoint, resolved))
        format_output(records, output_format, auto_format=not explicit_format)
        latency_ms = (_time.monotonic() - started) * 1000.0
        capture_filter = state.config.telemetry.capture_filter_text
        sink.emit(*_tev.query(
            endpoint=endpoint,
            has_filter=bool(resolved.filter),
            top=int(resolved.top) if resolved.top not in (None, "") else -1,
            skip=int(resolved.skip) if resolved.skip not in (None, "") else -1,
            all_pages=resolved.all_pages,
            status=200,
            latency_ms=latency_ms,
            filter_text=str(resolved.filter or "") if capture_filter else "",
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
        raise typer.Exit(1) from e


# ─── Helpers ─────────────────────────────────────────────────────────


def _list_queries(
    profile_name: str,
    queries_file: Path,
    *,
    tag: str | None = None,
    owner: str | None = None,
    freshness: str | None = None,
) -> None:
    """Print a table of saved queries for the active profile.

    Optional filters narrow the catalog by tag / owner / freshness.
    """
    if not queries_file.is_file():
        console.print(
            f"[dim]No saved queries for profile '{profile_name}'.[/dim]\n"
            f"[dim]Create them at: {queries_file}[/dim]\n"
        )
        _print_starter_example(queries_file)
        return

    queries = _load_saved_queries(queries_file)
    if not queries:
        console.print(f"[dim]{queries_file} has no queries defined.[/dim]")
        return

    entries = normalize_queries(queries)
    entries = filter_entries(entries, tag=tag, owner=owner, freshness=freshness)
    if not entries:
        active = ", ".join(
            f"{k}={v}" for k, v in (("tag", tag), ("owner", owner), ("freshness", freshness)) if v
        )
        console.print(
            f"[dim]No queries match {active or '(no filters)'}.[/dim]\n"
            "[dim]Run `bcli q list` with no filters to see the whole catalog.[/dim]"
        )
        return

    title_bits = [f"Saved queries — {profile_name}"]
    for label, value in (("tag", tag), ("owner", owner), ("freshness", freshness)):
        if value:
            title_bits.append(f"{label}={value}")
    table = Table(
        show_header=True,
        header_style="bold",
        title=" · ".join(title_bits),
    )
    table.add_column("Name", style="cyan")
    table.add_column("Endpoint")
    table.add_column("Tags")
    table.add_column("Owner")
    table.add_column("Description", max_width=50, overflow="fold")

    for entry in entries:
        decl_params = entry.params or {}
        param_summary = ", ".join(
            f"{k}{'*' if (isinstance(v, dict) and v.get('required', True)) else ''}"
            for k, v in decl_params.items()
        )
        endpoint_cell = entry.endpoint or "?"
        if param_summary:
            endpoint_cell = f"{endpoint_cell} ({param_summary})"
        table.add_row(
            entry.name,
            endpoint_cell,
            ", ".join(entry.tags) or "—",
            entry.owner or "—",
            entry.description,
        )
    console.print(table)
    console.print(
        "[dim]Run with: bcli q <name> [key=value ...]   "
        "Discover with: bcli q search <phrase>[/dim]"
    )


def _search_queries(profile_name: str, queries_file: Path, phrase: str) -> None:
    """Rank queries by name / alias / tag / description match."""
    queries = _load_saved_queries(queries_file)
    if not queries:
        console.print(f"[dim]No saved queries for profile '{profile_name}'.[/dim]")
        return

    entries = normalize_queries(queries)
    hits = search_entries(entries, phrase)
    if not hits:
        console.print(
            f"[yellow]No queries matched '{phrase}'.[/yellow]\n"
            "[dim]Try a shorter phrase or run `bcli q list` to browse.[/dim]"
        )
        raise typer.Exit(1)

    table = Table(
        show_header=True,
        header_style="bold",
        title=f"Search — '{phrase}'",
    )
    table.add_column("Score", justify="right", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Tags")
    table.add_column("Description", max_width=60, overflow="fold")
    for score, entry in hits:
        table.add_row(
            str(score),
            entry.name,
            ", ".join(entry.tags) or "—",
            entry.description,
        )
    console.print(table)
    console.print(
        "[dim]Open one with: bcli q info <name>     Run with: bcli q <name> ...[/dim]"
    )


def _query_info(profile_name: str, queries_file: Path, name: str) -> None:
    """Print full metadata for one query."""
    queries = _load_saved_queries(queries_file)
    canonical = resolve_query_name(queries, name)
    if canonical is None:
        console.print(
            f"[red]Saved query '{name}' not found.[/red] "
            f"Run `bcli q search '{name}'` to find similar."
        )
        raise typer.Exit(1)
    name = canonical

    entry = QueryEntry.from_raw(name, queries[name])

    console.print(f"[bold cyan]{entry.name}[/bold cyan]")
    if entry.description:
        console.print(f"  {entry.description}")
    console.print()
    if entry.aliases:
        console.print(f"  [dim]aliases:[/dim]    {', '.join(entry.aliases)}")
    if entry.tags:
        console.print(f"  [dim]tags:[/dim]       {', '.join(entry.tags)}")
    if entry.owner:
        console.print(f"  [dim]owner:[/dim]      {entry.owner}")
    if entry.freshness:
        console.print(f"  [dim]freshness:[/dim]  {entry.freshness}")
    if entry.endpoint:
        console.print(f"  [dim]endpoint:[/dim]   {entry.endpoint}")
    if entry.params:
        console.print("  [dim]params:[/dim]")
        for pname, pdef in entry.params.items():
            if isinstance(pdef, dict):
                bits = []
                if pdef.get("required"):
                    bits.append("required")
                if "default" in pdef:
                    bits.append(f"default={pdef['default']!r}")
                if pdef.get("type"):
                    bits.append(pdef["type"])
                if pdef.get("hint"):
                    bits.append(f"hint={pdef['hint']!r}")
                tail = f" ({', '.join(bits)})" if bits else ""
            else:
                tail = ""
            console.print(f"    {pname}{tail}")
    if entry.examples:
        console.print("  [dim]examples:[/dim]")
        for ex in entry.examples:
            console.print(f"    {ex}")
    if entry.related:
        console.print(f"  [dim]related:[/dim]    {', '.join(entry.related)}")


def _print_starter_example(queries_file: Path) -> None:
    console.print(
        "[dim]Example contents (replace with the entities and fields your "
        "profile actually uses):[/dim]\n"
        "[dim]queries:[/dim]\n"
        "[dim]  customer-by-name:[/dim]\n"
        "[dim]    description: Look up a customer by display name[/dim]\n"
        "[dim]    endpoint: customers[/dim]\n"
        "[dim]    params:[/dim]\n"
        "[dim]      name: {required: true}[/dim]\n"
        "[dim]    filter: \"displayName eq '${{ params.name }}'\"[/dim]\n"
        "[dim]    select: number,displayName,email[/dim]\n"
        "[dim]    top: 25[/dim]\n"
    )


def _load_saved_queries(queries_file: Path) -> dict[str, dict[str, Any]]:
    """Parse a saved-queries YAML file. Returns an empty dict if missing."""
    try:
        return load_catalog(queries_file)
    except QueryCatalogError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e


def _resolve_params(
    declared: dict[str, Any],
    cli_args: list[str],
) -> dict[str, Any]:
    """Turn ``key=value`` argv into typed values, then merge + validate.

    The ``key=value`` splitting and type-guessing is CLI-specific (a
    non-CLI caller already has typed values); everything past that —
    merging with declared defaults, required checks, type/pattern/enum
    validation — is :func:`bcli.queries.resolve_params`, which raises
    :class:`QueryParamError` instead of exiting directly so it stays usable
    outside a CLI context.
    """
    from bcli_cli.commands.batch_cmd import _smart_parse_value

    supplied: dict[str, Any] = {}
    for arg in cli_args:
        if "=" not in arg:
            console.print(f"[red]Invalid parameter '{arg}' — expected key=value.[/red]")
            raise typer.Exit(1)
        key, _, raw_value = arg.partition("=")
        supplied[key.strip()] = _smart_parse_value(raw_value.strip())

    try:
        return _sdk_resolve_params(declared, supplied)
    except QueryParamError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e


def _expand_query(spec: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Dict-returning wrapper over :func:`bcli.queries.expand_query`.

    Kept for existing callers/tests that expect the historical
    dict-in/dict-out shape; only includes the OData fields the input
    ``spec`` actually declared, mirroring what the pre-extraction
    implementation returned.
    """
    resolved = expand_query(spec, params)
    return {key: getattr(resolved, key) for key in ODATA_FIELDS if key in spec}


def _print_resolved(name: str, endpoint: Any, resolved: ResolvedQuery) -> None:
    """Show the OData equivalent of a resolved saved query."""
    console.print(f"[bold]{name}[/bold] → GET {endpoint}")
    for key in ODATA_FIELDS:
        if key == "endpoint":
            continue
        value = getattr(resolved, key)
        if value is not None:
            console.print(f"  {key}: {value}")


async def _run_saved_query(endpoint: str, resolved: ResolvedQuery) -> list[dict]:
    """Execute the resolved query against the active profile."""
    query = resolved.to_query()

    async with state.make_async_client() as client:
        if resolved.all_pages:
            records: list[dict] = []
            bound = client.query(endpoint)
            for f in query._params.filters:
                bound.filter(f)
            for s in query._params.selects:
                bound.select(s)
            for ex in query._params.expands:
                bound.expand(ex)
            if query._params.orderby:
                bound.orderby(query._params.orderby)
            pages = await bound.pages()
            async for page in pages:
                records.extend(page)
            return records

        response = await client.get(endpoint, query=query)
        return response.value
