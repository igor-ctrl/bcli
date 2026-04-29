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
            type: string | integer | number | boolean   # default: string
            pattern: "^[A-Z0-9]{2,8}$"                  # string-only regex
            min: 1                                       # numeric lower bound
            max: 1000                                    # numeric upper bound
            enum: ["Open", "Posted"]                    # allowed literal set
        filter:  "displayName eq '${{ params.name }}'"
        select:  "number,displayName,email,phoneNumber"
        orderby: "displayName asc"
        top: 50
        all: false

Defense-in-depth notes:

* ``type`` / ``pattern`` / ``min`` / ``max`` / ``enum`` are validated *before*
  any HTTP call. Bad input fails locally with a clear message instead of
  hitting BC and getting back a 400.
* When a string-typed param is interpolated into the ``filter:`` field, OData
  single-quote escaping is applied so a value like ``193208' or 1 eq 1--``
  cannot break out of its string literal. The escape is scoped to the filter
  context — other fields (``select``, ``top``, etc.) keep raw values.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from bcli.config._defaults import CONFIG_DIR
from bcli.odata import escape_odata_string
from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

console = Console(stderr=True)

QUERIES_DIR = CONFIG_DIR / "queries"

# Param types we accept in saved-query declarations.
_VALID_TYPES = frozenset({"string", "integer", "number", "boolean"})


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

    import time as _time

    from bcli.telemetry import events as _tev

    sink = state.telemetry
    started = _time.monotonic()
    try:
        records = asyncio.run(_run_saved_query(endpoint, resolved))
        format_output(records, output_format)
        latency_ms = (_time.monotonic() - started) * 1000.0
        capture_filter = state.config.telemetry.capture_filter_text
        sink.emit(*_tev.query(
            endpoint=endpoint,
            has_filter=bool(resolved.get("filter")),
            top=int(resolved.get("top", -1)) if resolved.get("top") not in (None, "") else -1,
            skip=int(resolved.get("skip", -1)) if resolved.get("skip") not in (None, "") else -1,
            all_pages=bool(resolved.get("all")),
            status=200,
            latency_ms=latency_ms,
            filter_text=str(resolved.get("filter") or "") if capture_filter else "",
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
    """Merge declared defaults with ``key=value`` CLI overrides; validate.

    Validation order:
      1. Defaults and CLI args are merged.
      2. Required params are checked.
      3. Each value is coerced/checked against its declared ``type``,
         ``pattern``, ``min``, ``max``, ``enum`` constraints.

    Failures exit with a clear, non-developer-friendly error message before any
    HTTP call.
    """
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

    for key, defn in (declared or {}).items():
        if not isinstance(defn, dict) or key not in resolved:
            continue
        resolved[key] = _validate_param(key, resolved[key], defn)

    return resolved


def _validate_param(key: str, value: Any, defn: dict[str, Any]) -> Any:
    """Coerce and validate a single param against its declared constraints.

    When ``type`` is omitted the value is left untouched (preserves the smart
    parsing applied at CLI parse time, e.g. ``top=5`` stays an ``int``). When
    ``type`` is declared, the value is coerced to that type and the matching
    constraints (``pattern``, ``min``, ``max``, ``enum``) are enforced.

    Exits via Typer on validation failure with a message that names the param
    and the rule that failed.
    """
    type_decl = defn.get("type")
    if type_decl is not None and type_decl not in _VALID_TYPES:
        console.print(
            f"[red]Saved-query schema error: param '{key}' declares "
            f"unknown type '{type_decl}'. Valid: {sorted(_VALID_TYPES)}.[/red]"
        )
        raise typer.Exit(1)

    if type_decl == "integer":
        try:
            value = int(value)
        except (TypeError, ValueError):
            console.print(
                f"[red]Param '{key}' must be an integer; got {value!r}.[/red]"
            )
            raise typer.Exit(1) from None
    elif type_decl == "number":
        try:
            value = float(value)
        except (TypeError, ValueError):
            console.print(
                f"[red]Param '{key}' must be a number; got {value!r}.[/red]"
            )
            raise typer.Exit(1) from None
    elif type_decl == "boolean":
        if isinstance(value, bool):
            pass
        elif isinstance(value, str) and value.lower() in {"true", "false"}:
            value = value.lower() == "true"
        else:
            console.print(
                f"[red]Param '{key}' must be a boolean (true/false); "
                f"got {value!r}.[/red]"
            )
            raise typer.Exit(1)
    elif type_decl == "string":
        value = str(value)

    enum = defn.get("enum")
    if enum is not None:
        if not isinstance(enum, list):
            console.print(
                f"[red]Saved-query schema error: param '{key}' enum must be a list.[/red]"
            )
            raise typer.Exit(1)
        if value not in enum:
            console.print(
                f"[red]Param '{key}'={value!r} is not in allowed set {enum}.[/red]"
            )
            raise typer.Exit(1)

    pattern = defn.get("pattern")
    if pattern is not None:
        if type_decl is not None and type_decl != "string":
            console.print(
                f"[red]Saved-query schema error: param '{key}' uses 'pattern' "
                f"with type '{type_decl}' (only 'string' supports pattern).[/red]"
            )
            raise typer.Exit(1)
        try:
            if not re.fullmatch(pattern, str(value)):
                console.print(
                    f"[red]Param '{key}'={value!r} does not match pattern "
                    f"{pattern!r}.[/red]"
                )
                raise typer.Exit(1)
        except re.error as e:
            console.print(
                f"[red]Saved-query schema error: param '{key}' has invalid "
                f"regex {pattern!r}: {e}[/red]"
            )
            raise typer.Exit(1) from e

    if type_decl in ("integer", "number"):
        min_v = defn.get("min")
        if min_v is not None and value < min_v:
            console.print(
                f"[red]Param '{key}'={value} is below min ({min_v}).[/red]"
            )
            raise typer.Exit(1)
        max_v = defn.get("max")
        if max_v is not None and value > max_v:
            console.print(
                f"[red]Param '{key}'={value} exceeds max ({max_v}).[/red]"
            )
            raise typer.Exit(1)

    return value


def _expand_query(spec: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``${{ params.X }}`` references inside the saved query spec.

    The ``filter:`` field is re-resolved with OData-escaped string params so a
    value containing ``'`` cannot break out of the surrounding string literal.
    Other fields (``select``, ``orderby``, ``top``, ``skip``, ``all``,
    ``endpoint``) are resolved with raw params — they don't sit inside OData
    string literals, so escaping there would corrupt the value (e.g. an
    apostrophe in a vendor name passed via ``--show``).
    """
    from bcli.workflow._models import WorkflowContext
    from bcli.workflow._resolver import resolve_references

    context = WorkflowContext(params=params)
    expanded = resolve_references(spec, context)

    filter_template = spec.get("filter")
    if isinstance(filter_template, str) and "${{" in filter_template:
        escaped = {
            k: (escape_odata_string(v) if isinstance(v, str) else v)
            for k, v in params.items()
        }
        filter_ctx = WorkflowContext(params=escaped)
        expanded["filter"] = resolve_references(filter_template, filter_ctx)

    return expanded


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
