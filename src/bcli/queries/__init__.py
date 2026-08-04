"""Reusable saved-query engine.

Catalog loading, parameter validation, ``${{ }}`` expansion, and discovery
(list/search/info) — the whole pipeline behind ``bcli q <name> key=value``,
extracted from the CLI so any consumer (a workflow step, a remote MCP
server) can run a named, parametrised OData query without hand-rolling it
again or importing CLI code to get at it.

Nothing here performs an HTTP call or talks to a console/terminal — see
:mod:`bcli_cli.commands.query_cmd` for the thin CLI layer built on top of
this package.

Typical flow::

    catalog = load_catalog(Path("~/.config/bcli/queries/tech-prod.yaml"))
    name = resolve_query_name(catalog, "customer-by-name")  # follows aliases
    spec = catalog[name]
    params = resolve_params(spec.get("params", {}), {"name": "Fabrikam"})
    resolved = expand_query(spec, params)          # -> ResolvedQuery
    query = resolved.to_query()                     # -> bcli.odata.Query
    response = await client.get(resolved.endpoint, query=query)
"""

from __future__ import annotations

from bcli.queries._catalog import (
    RESERVED_QUERY_NAMES,
    load_catalog,
    load_catalog_from_mapping,
    resolve_alias,
    resolve_query_name,
)
from bcli.queries._errors import QueryCatalogError, QueryError, QueryParamError
from bcli.queries._expand import ODATA_FIELDS, ResolvedQuery, expand_query
from bcli.queries._params import VALID_PARAM_TYPES, resolve_params, validate_param
from bcli.workflow._query_search import (
    QueryEntry,
    filter_entries,
    normalize_queries,
    search_entries,
)

__all__ = [
    "ODATA_FIELDS",
    "RESERVED_QUERY_NAMES",
    "VALID_PARAM_TYPES",
    "QueryCatalogError",
    "QueryEntry",
    "QueryError",
    "QueryParamError",
    "ResolvedQuery",
    "expand_query",
    "filter_entries",
    "load_catalog",
    "load_catalog_from_mapping",
    "normalize_queries",
    "resolve_alias",
    "resolve_params",
    "resolve_query_name",
    "search_entries",
    "validate_param",
]
