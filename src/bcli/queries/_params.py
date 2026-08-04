"""Saved-query parameter merging + pre-HTTP validation.

Validation happens entirely client-side, before any request is built. This
is the property that keeps a malformed or hostile value — e.g. an
unescaped ``'`` meant for an OData ``$filter`` — from ever reaching
Business Central; a bad value fails locally with a clear message instead of
round-tripping to BC for a 400. See :mod:`bcli.queries._expand` for the
OData-escaping half of that story (applied once a value has already passed
here).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from bcli.queries._errors import QueryParamError

# Param types a saved-query declaration may declare.
VALID_PARAM_TYPES = frozenset({"string", "integer", "number", "boolean"})


def resolve_params(
    declared: Mapping[str, Any] | None,
    supplied: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge declared defaults with caller-supplied values, then validate.

    ``supplied`` values are already Python-typed (an ``int`` for a limit, a
    ``bool`` for a flag, …) — turning raw ``key=value`` CLI strings into
    typed values is the caller's job. A non-CLI caller (a workflow step, an
    MCP tool invocation) already has typed values and shouldn't have to
    round-trip through strings just to reach this function.

    Validation order:
      1. Defaults and supplied values are merged (supplied wins).
      2. Required params are checked.
      3. Each value is coerced/checked against its declared ``type``,
         ``pattern``, ``min``, ``max``, ``enum`` constraints.

    Raises :class:`QueryParamError` on the first failure.
    """
    declared = declared or {}
    resolved: dict[str, Any] = {}

    for key, defn in declared.items():
        if isinstance(defn, dict):
            if "default" in defn and defn["default"] is not None:
                resolved[key] = defn["default"]
        else:
            resolved[key] = defn

    resolved.update(supplied or {})

    for key, defn in declared.items():
        required = (isinstance(defn, dict) and defn.get("required", False)) or (
            not isinstance(defn, dict) and defn is None
        )
        if required and key not in resolved:
            raise QueryParamError(
                key,
                f"Missing required parameter '{key}'. "
                f"Pass it as: bcli q <name> {key}=<value>",
                kind="missing_required",
            )

    for key, defn in declared.items():
        if not isinstance(defn, dict) or key not in resolved:
            continue
        resolved[key] = validate_param(key, resolved[key], defn)

    return resolved


def validate_param(key: str, value: Any, defn: Mapping[str, Any]) -> Any:
    """Coerce and validate a single param against its declared constraints.

    When ``type`` is omitted the value is left untouched (preserves
    whatever typing the caller already applied, e.g. ``top=5`` staying an
    ``int``). When ``type`` is declared, the value is coerced to that type
    and the matching constraints (``pattern``, ``min``, ``max``, ``enum``)
    are enforced.

    Raises :class:`QueryParamError` naming the param and the rule that
    failed. ``kind="schema"`` means the *catalog* declared something
    invalid (an unknown type, a non-list enum, a pattern on a non-string
    type, a bad regex) — not the caller's input.
    """
    type_decl = defn.get("type")
    if type_decl is not None and type_decl not in VALID_PARAM_TYPES:
        raise QueryParamError(
            key,
            f"Saved-query schema error: param '{key}' declares unknown type "
            f"'{type_decl}'. Valid: {sorted(VALID_PARAM_TYPES)}.",
            kind="schema",
        )

    if type_decl == "integer":
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise QueryParamError(
                key, f"Param '{key}' must be an integer; got {value!r}."
            ) from None
    elif type_decl == "number":
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise QueryParamError(
                key, f"Param '{key}' must be a number; got {value!r}."
            ) from None
    elif type_decl == "boolean":
        if isinstance(value, bool):
            pass
        elif isinstance(value, str) and value.lower() in {"true", "false"}:
            value = value.lower() == "true"
        else:
            raise QueryParamError(
                key, f"Param '{key}' must be a boolean (true/false); got {value!r}."
            )
    elif type_decl == "string":
        value = str(value)

    enum = defn.get("enum")
    if enum is not None:
        if not isinstance(enum, list):
            raise QueryParamError(
                key,
                f"Saved-query schema error: param '{key}' enum must be a list.",
                kind="schema",
            )
        if value not in enum:
            raise QueryParamError(
                key, f"Param '{key}'={value!r} is not in allowed set {enum}."
            )

    pattern = defn.get("pattern")
    if pattern is not None:
        if type_decl is not None and type_decl != "string":
            raise QueryParamError(
                key,
                f"Saved-query schema error: param '{key}' uses 'pattern' with "
                f"type '{type_decl}' (only 'string' supports pattern).",
                kind="schema",
            )
        try:
            matched = re.fullmatch(pattern, str(value))
        except re.error as e:
            raise QueryParamError(
                key,
                f"Saved-query schema error: param '{key}' has invalid regex "
                f"{pattern!r}: {e}",
                kind="schema",
            ) from e
        if not matched:
            raise QueryParamError(
                key, f"Param '{key}'={value!r} does not match pattern {pattern!r}."
            )

    if type_decl in ("integer", "number"):
        min_v = defn.get("min")
        if min_v is not None and value < min_v:
            raise QueryParamError(key, f"Param '{key}'={value} is below min ({min_v}).")
        max_v = defn.get("max")
        if max_v is not None and value > max_v:
            raise QueryParamError(key, f"Param '{key}'={value} exceeds max ({max_v}).")

    return value
