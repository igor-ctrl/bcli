"""Fluent OData query builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote


@dataclass
class QueryParams:
    """Accumulated OData query parameters."""

    filters: list[str] = field(default_factory=list)
    selects: list[str] = field(default_factory=list)
    expands: list[str] = field(default_factory=list)
    orderby: str | None = None
    top: int | None = None
    skip: int | None = None
    count: bool = False


class Query:
    """Fluent OData query builder.

    Usage:
        q = Query().filter("status eq 'Active'").select("id", "name").top(10)
        params = q.to_params()
    """

    def __init__(self) -> None:
        self._params = QueryParams()

    def filter(self, expression: str) -> Query:
        """Add a $filter expression. Multiple filters are ANDed."""
        self._params.filters.append(expression)
        return self

    def select(self, *fields: str) -> Query:
        """Set $select fields."""
        self._params.selects.extend(fields)
        return self

    def expand(self, *navigations: str) -> Query:
        """Set $expand navigation properties."""
        self._params.expands.extend(navigations)
        return self

    def orderby(self, expression: str) -> Query:
        """Set $orderby expression."""
        self._params.orderby = expression
        return self

    def top(self, n: int) -> Query:
        """Set $top (limit)."""
        self._params.top = n
        return self

    def skip(self, n: int) -> Query:
        """Set $skip (offset)."""
        self._params.skip = n
        return self

    def count(self, enabled: bool = True) -> Query:
        """Enable $count."""
        self._params.count = enabled
        return self

    def to_params(self) -> dict[str, str]:
        """Convert to a dict of OData query parameters."""
        params: dict[str, str] = {}

        if self._params.filters:
            combined = " and ".join(f"({f})" for f in self._params.filters)
            params["$filter"] = combined

        if self._params.selects:
            params["$select"] = ",".join(self._params.selects)

        if self._params.expands:
            params["$expand"] = ",".join(self._params.expands)

        if self._params.orderby:
            params["$orderby"] = self._params.orderby

        if self._params.top is not None:
            params["$top"] = str(self._params.top)

        if self._params.skip is not None:
            params["$skip"] = str(self._params.skip)

        if self._params.count:
            params["$count"] = "true"

        return params

    def to_query_string(self) -> str:
        """Convert to a URL query string."""
        params = self.to_params()
        if not params:
            return ""
        parts = [f"{k}={quote(v, safe=',()\'')}" for k, v in params.items()]
        return "?" + "&".join(parts)

    @property
    def is_empty(self) -> bool:
        """True if no query parameters have been set."""
        p = self._params
        return (
            not p.filters
            and not p.selects
            and not p.expands
            and p.orderby is None
            and p.top is None
            and p.skip is None
            and not p.count
        )
