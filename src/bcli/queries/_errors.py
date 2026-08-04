"""Exceptions raised by the saved-query SDK layer.

These are purely local, pre-HTTP failures — a malformed catalog, a missing
or mistyped parameter — never a response from Business Central. They're
kept separate from the transport-facing errors in :mod:`bcli.errors` (which
map to HTTP status codes) even though they share the same base class, so a
caller can tell "this never left the machine" from "BC rejected it" just by
the exception type. A CLI or an MCP server catches these and decides how to
present them; nothing in this module talks to a console.
"""

from __future__ import annotations

from bcli.errors import BCLIError


class QueryError(BCLIError):
    """Base class for saved-query errors that never reach Business Central."""


class QueryCatalogError(QueryError):
    """A saved-query catalog (YAML file or already-parsed mapping) is malformed."""


class QueryParamError(QueryError):
    """A supplied parameter is missing, mistyped, or fails its declared constraint.

    ``key`` names the offending parameter. ``kind`` is a coarse,
    machine-readable discriminant for callers that want to branch without
    parsing the message: ``"schema"`` means the *catalog* is malformed (an
    unknown declared type, a non-list enum, …) — a bundle-authoring bug, not
    a caller mistake; ``"missing_required"`` and the default ``"value"``
    describe the caller's supplied value.
    """

    def __init__(self, key: str, message: str, *, kind: str = "value") -> None:
        self.key = key
        self.kind = kind
        super().__init__(message)
