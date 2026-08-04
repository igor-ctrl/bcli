"""Resolve a saved-query spec + supplied params into a request spec.

Nothing here performs an HTTP call: :func:`expand_query` returns a
:class:`ResolvedQuery` — an endpoint plus OData options — that the caller
turns into a request however it likes. ``ResolvedQuery.to_query()`` builds
a :class:`bcli.odata.Query` for callers that already hold a BC client
(the CLI, a workflow step, a remote MCP tool); a caller with different
needs can just read the fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bcli.odata import Query, escape_odata_string
from bcli.workflow import WorkflowContext, resolve_references

# The seven fields of a saved-query spec that describe the OData request
# itself, plus ``endpoint``. Everything else in a spec (description,
# aliases, tags, owner, …) is discoverability metadata handled by
# :mod:`bcli.workflow._query_search`, not by expansion.
ODATA_FIELDS = ("endpoint", "filter", "select", "expand", "orderby", "top", "skip", "all")


@dataclass(frozen=True)
class ResolvedQuery:
    """A saved query after ``${{ params.X }}`` substitution — ready to execute."""

    endpoint: Any = None
    filter: Any = None
    select: Any = None
    expand: Any = None
    orderby: Any = None
    top: Any = None
    skip: Any = None
    all: Any = None

    @property
    def all_pages(self) -> bool:
        """Whether the query should page through every result (the ``all:`` flag)."""
        return bool(self.all)

    def to_query(self) -> Query:
        """Build a :class:`bcli.odata.Query` from the resolved OData fields."""
        query = Query()
        if self.filter:
            query.filter(str(self.filter))
        if self.select:
            query.select(*[s.strip() for s in str(self.select).split(",")])
        if self.expand:
            query.expand(*[e.strip() for e in str(self.expand).split(",")])
        if self.orderby:
            query.orderby(str(self.orderby))
        if self.top is not None:
            query.top(int(self.top))
        if self.skip is not None:
            query.skip(int(self.skip))
        return query


def expand_query(spec: Mapping[str, Any], params: Mapping[str, Any]) -> ResolvedQuery:
    """Resolve ``${{ params.X }}`` references inside a saved-query spec.

    The ``filter:`` field is re-resolved with OData-escaped string params so
    a value containing ``'`` cannot break out of the surrounding string
    literal. Other fields (``select``, ``orderby``, ``top``, ``skip``,
    ``all``, ``endpoint``) are resolved with raw params — they don't sit
    inside OData string literals, so escaping there would corrupt the value
    (e.g. an apostrophe in a vendor name passed through for display).
    """
    subset = {k: spec[k] for k in ODATA_FIELDS if k in spec}
    context = WorkflowContext(params=dict(params))
    expanded = resolve_references(subset, context)

    filter_template = spec.get("filter")
    if isinstance(filter_template, str) and "${{" in filter_template:
        escaped_params = {
            k: (escape_odata_string(v) if isinstance(v, str) else v) for k, v in params.items()
        }
        filter_ctx = WorkflowContext(params=escaped_params)
        expanded["filter"] = resolve_references(filter_template, filter_ctx)

    return ResolvedQuery(**expanded)
