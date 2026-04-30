"""FastMCP server with the day-1 bcli tool surface (4 tools).

Design constraints:

* Token economy. Tool docstrings are short and concrete. ``query`` caps
  ``top`` at 1000 with a sane default of 50 so an unbounded request can't
  pull a whole table into context.
* Subprocess only. Every tool delegates to ``run_bcli_json`` so profile
  resolution, auth, retry, telemetry, and the registry filters that
  ``bcli_cli._state`` applies (``disable_standard_api``,
  ``allowed_categories``) are inherited for free.
* Read-only. Mutating commands (post/patch/delete, batch, attach upload)
  are deliberately NOT exposed — they go through the CLI directly where
  the existing ``disable_writes`` prompt protects.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from bcli_mcp._runner import run_bcli_json

mcp = FastMCP("bcli")

# Hard caps so an agent can't accidentally pull a whole table.
_QUERY_DEFAULT_TOP = 50
_QUERY_MAX_TOP = 1000


@mcp.tool()
async def query(
    entity: str,
    filter: str | None = None,
    select: str | None = None,
    top: int | None = None,
    skip: int | None = None,
    orderby: str | None = None,
    expand: str | None = None,
    record_id: str | None = None,
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Run an OData query against a Business Central entity.

    Returns a list of records. ``top`` defaults to 50 and is capped at
    1000 — for browse-style "show me everything" use the bcli CLI directly.
    Use ``select`` to limit fields and keep responses small.

    ``entity`` is the OData entity-set name (e.g. ``customers``,
    ``salesInvoices``). Use ``list_endpoints`` to discover what's
    available; ``describe_endpoint`` to see the field shape.
    """
    effective_top = top if top is not None else _QUERY_DEFAULT_TOP
    if effective_top < 1:
        effective_top = 1
    if effective_top > _QUERY_MAX_TOP:
        effective_top = _QUERY_MAX_TOP

    args: list[str] = ["get", entity]
    if record_id:
        args.append(record_id)
    if filter:
        args.extend(["--filter", filter])
    if select:
        args.extend(["--select", select])
    args.extend(["--top", str(effective_top)])
    if skip is not None:
        args.extend(["--skip", str(skip)])
    if orderby:
        args.extend(["--orderby", orderby])
    if expand:
        args.extend(["--expand", expand])
    if publisher:
        args.extend(["--publisher", publisher])
    if group:
        args.extend(["--group", group])
    if version:
        args.extend(["--version", version])

    result = run_bcli_json(*args, profile=profile)
    # `bcli get <entity>` returns a list. `bcli get <entity> <id>`
    # returns a single object — wrap so the tool's return type is stable.
    if isinstance(result, dict):
        return [result]
    return result


@mcp.tool()
async def list_endpoints(
    category: str | None = None,
    custom_only: bool = False,
    standard_only: bool = False,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List Business Central entities the active profile can reach.

    Returns ``[{name, category, custom, supported_ops, key_field,
    publisher, group, version, description}]``. Honours profile-level
    filters (``disable_standard_api``, ``allowed_categories``).
    """
    args = ["endpoint", "list"]
    if custom_only:
        args.append("--custom")
    if standard_only:
        args.append("--standard")
    if category:
        args.extend(["--category", category])
    return run_bcli_json(*args, profile=profile)


@mcp.tool()
async def describe_endpoint(
    name: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Show fields, key, supported operations, and route for one entity.

    ``fields`` is populated only after ``bcli endpoint fields <name>`` has
    been run for the entity (it costs one BC API call). Empty list = not
    yet discovered.
    """
    return run_bcli_json("endpoint", "info", name, profile=profile)


@mcp.tool()
async def list_companies(
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List companies on the active environment.

    Returns ``[{id, name, alias, is_default}]``. ``alias`` is the local
    nickname configured via ``bcli company alias …`` (null if unset).
    """
    return run_bcli_json("company", "list", profile=profile)
