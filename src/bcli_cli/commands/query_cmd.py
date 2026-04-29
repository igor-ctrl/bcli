"""bcli q — saved-query aliases for the daily questions a domain user asks.

Hides OData syntax behind named, parametrised queries stored per-profile in
``~/.config/bcli/queries/{profile}.yaml``. A non-developer user can run
``bcli q customer-by-name name=Fabrikam`` instead of remembering that the
filter field is ``displayName`` and the operator is ``eq``.

YAML schema (per file)::

    queries:
      <name>:
        description: "human description"
        endpoint: <entitySetName>
        params:
          <key>:
            required: true
            default: <value>
        filter:  "displayName eq '${{ params.name }}'"
        select:  "number,displayName,email,phoneNumber"
        orderby: "displayName asc"
        top: 50
        all: false
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from bcli.config._defaults import CONFIG_DIR
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
    """
    profile_name = state.active_profile_name
    queries_file = QUERIES_DIR / f"{profile_name}.yaml"

    if name is None:
        _list_queries(profile_name, queries_file)
        return

    saved = _load_saved_queries(queries_file)
    if name not in saved:
        available = ", ".join(sorted(saved.keys())) or "(none)"
        console.print(
            f"[red]Saved query '{name}' not found.[/red] Available: {available}\n"
            f"[dim]Edit {queries_file} to add one.[/dim]"
        )
        raise typer.Exit(1)

    spec = saved[name]
    resolved_params = _resolve_params(spec.get("params", {}), params or [])
    resolved = _expand_query(spec, resolved_params)

    endpoint = resolved.get("endpoint")
    if not endpoint:
        console.print(f"[red]Saved query '{name}' has no 'endpoint'.[/red]")
        raise typer.Exit(1)

    if show:
        _print_resolved(name, endpoint, resolved)
        return

    output_format = format or state.format
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    if state.dry_run:
        _print_resolved(name, endpoint, resolved)
        console.print("[yellow]--dry-run: would execute, skipping.[/yellow]")
        return

    try:
        records = asyncio.run(_run_saved_query(endpoint, resolved))
        format_output(records, output_format)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e


# ─── Helpers ─────────────────────────────────────────────────────────


def _list_queries(profile_name: str, queries_file: Path) -> None:
    """Print a table of saved queries for the active profile."""
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

    table = Table(show_header=True, header_style="bold", title=f"Saved queries — {profile_name}")
    table.add_column("Name", style="cyan")
    table.add_column("Endpoint")
    table.add_column("Params")
    table.add_column("Description", max_width=60)

    for q_name in sorted(queries.keys()):
        spec = queries[q_name]
        decl_params = spec.get("params") or {}
        param_summary = ", ".join(
            f"{k}{'*' if (isinstance(v, dict) and v.get('required', True)) else ''}"
            for k, v in decl_params.items()
        )
        table.add_row(
            q_name,
            spec.get("endpoint", "?"),
            param_summary or "—",
            spec.get("description", ""),
        )
    console.print(table)
    console.print("[dim]Run with: bcli q <name> [key=value ...][/dim]")


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
    if not queries_file.is_file():
        return {}
    try:
        import yaml
    except ImportError as e:
        console.print("[red]PyYAML is required for saved queries.[/red]")
        raise typer.Exit(1) from e

    try:
        raw = yaml.safe_load(queries_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        console.print(f"[red]Failed to parse {queries_file}:[/red] {e}")
        raise typer.Exit(1) from e

    queries = raw.get("queries", {})
    if not isinstance(queries, dict):
        console.print(f"[red]{queries_file}: 'queries' must be a mapping.[/red]")
        raise typer.Exit(1)
    return queries


def _resolve_params(
    declared: dict[str, Any],
    cli_args: list[str],
) -> dict[str, Any]:
    """Merge declared defaults with ``key=value`` CLI overrides; validate required."""
    from bcli_cli.commands.batch_cmd import _smart_parse_value

    resolved: dict[str, Any] = {}

    for key, defn in (declared or {}).items():
        if isinstance(defn, dict):
            if "default" in defn and defn["default"] is not None:
                resolved[key] = defn["default"]
        else:
            resolved[key] = defn

    for arg in cli_args:
        if "=" not in arg:
            console.print(f"[red]Invalid parameter '{arg}' — expected key=value.[/red]")
            raise typer.Exit(1)
        key, _, raw_value = arg.partition("=")
        resolved[key.strip()] = _smart_parse_value(raw_value.strip())

    for key, defn in (declared or {}).items():
        required = (
            isinstance(defn, dict) and defn.get("required", False)
        ) or (
            not isinstance(defn, dict) and defn is None
        )
        if required and key not in resolved:
            console.print(
                f"[red]Missing required parameter '{key}'.[/red] "
                f"Pass it as: bcli q <name> {key}=<value>"
            )
            raise typer.Exit(1)

    return resolved


def _expand_query(spec: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``${{ params.X }}`` references inside the saved query spec."""
    from bcli.workflow._models import WorkflowContext
    from bcli.workflow._resolver import resolve_references

    context = WorkflowContext(params=params)
    return resolve_references(spec, context)


def _print_resolved(name: str, endpoint: str, resolved: dict[str, Any]) -> None:
    """Show the OData equivalent of a resolved saved query."""
    console.print(f"[bold]{name}[/bold] → GET {endpoint}")
    for key in ("filter", "select", "expand", "orderby", "top", "skip", "all"):
        if key in resolved:
            console.print(f"  {key}: {resolved[key]}")


async def _run_saved_query(endpoint: str, resolved: dict[str, Any]) -> list[dict]:
    """Execute the resolved query against the active profile."""
    from bcli.odata._query import Query

    query = Query()
    if resolved.get("filter"):
        query.filter(str(resolved["filter"]))
    if resolved.get("select"):
        query.select(*[s.strip() for s in str(resolved["select"]).split(",")])
    if resolved.get("expand"):
        query.expand(*[e.strip() for e in str(resolved["expand"]).split(",")])
    if resolved.get("orderby"):
        query.orderby(str(resolved["orderby"]))
    if resolved.get("top") is not None:
        query.top(int(resolved["top"]))
    if resolved.get("skip") is not None:
        query.skip(int(resolved["skip"]))

    all_pages = bool(resolved.get("all"))

    async with state.make_async_client() as client:
        if all_pages:
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
