"""Extract candidate field names from an OData ``$filter`` expression.

Used to validate a user's ``--filter`` against the entity's known field set
*before* the HTTP request, so we can surface a helpful suggestion
(``Did you mean: displayName?`` when the user typed ``name``) instead of a
raw 400 from Business Central.

The parser is intentionally conservative: it never touches the filter that
gets sent on the wire, it only inspects the string. False negatives (failing
to flag a typo) are preferable to false positives (rejecting a valid filter).
"""

from __future__ import annotations

import difflib
import re

# OData reserved tokens — operators, literals, type aliases.
_RESERVED: frozenset[str] = frozenset(
    {
        # Comparison / logical operators
        "eq", "ne", "gt", "ge", "lt", "le", "and", "or", "not",
        # Arithmetic
        "add", "sub", "mul", "div", "mod",
        # Membership / null
        "has", "in", "null", "true", "false",
        # Type aliases that occasionally appear in filters
        "any", "all", "asc", "desc",
    }
)

# OData built-in functions; the bare name is reserved when followed by ``(``.
_FUNCTIONS: frozenset[str] = frozenset(
    {
        "startswith", "endswith", "contains", "concat",
        "indexof", "length", "substring", "tolower", "toupper", "trim",
        "year", "month", "day", "hour", "minute", "second", "fractionalseconds",
        "date", "time", "totaloffsetminutes", "now", "mindatetime", "maxdatetime",
        "round", "floor", "ceiling",
        "cast", "isof",
    }
)

# Match an identifier OR a single/double-quoted string literal. We scan in
# order so we can throw away the contents of literals before treating
# identifiers as candidates.
_TOKEN = re.compile(
    r"""
    '(?:''|[^'])*'        # OData uses doubled single-quotes for escaping
  | "(?:\\.|[^"])*"
  | \b[A-Za-z_][A-Za-z0-9_]*\b
    """,
    re.VERBOSE,
)


def extract_field_references(filter_expr: str) -> list[str]:
    """Return the unique identifier-like tokens from an OData filter.

    Strips quoted literals, OData operators, and function names. The result
    is a best-effort list of candidate property references — order-preserved,
    de-duplicated case-insensitively.
    """
    seen: dict[str, str] = {}
    for match in _TOKEN.finditer(filter_expr):
        tok = match.group(0)
        if tok.startswith(("'", '"')):
            continue  # string literal
        lower = tok.lower()
        if lower in _RESERVED:
            continue
        # Skip function-call names (``contains(name,...)``).
        end = match.end()
        rest = filter_expr[end:].lstrip()
        if rest.startswith("(") and lower in _FUNCTIONS:
            continue
        # Numeric-looking tokens (none, since regex is alpha-led) are filtered
        # already. Drop anything that's just digits to be safe.
        if tok.isdigit():
            continue
        seen.setdefault(lower, tok)
    return list(seen.values())


def suggest_field(unknown: str, known: list[str], *, max_suggestions: int = 1) -> list[str]:
    """Return the closest known field name(s) for an unknown identifier.

    Uses :func:`difflib.get_close_matches` with a generous cutoff so common
    typos (``namee`` → ``name``) get suggested even when the literal edit
    distance is small. Falls back to a substring pass when difflib produces
    nothing — handy for short forms like ``serial`` matching the longer
    ``serialNumber``.
    """
    if not known:
        return []
    lower_known = {k.lower(): k for k in known}
    matches = difflib.get_close_matches(
        unknown.lower(), list(lower_known.keys()), n=max_suggestions, cutoff=0.5,
    )
    if matches:
        return [lower_known[m] for m in matches]
    # Substring fallback — short forms like 'serial' matching 'serialNumber'.
    needle = unknown.lower()
    hits = [orig for low, orig in lower_known.items() if needle and needle in low]
    return hits[:max_suggestions]


def validate_filter_fields(
    filter_expr: str | None,
    known_fields: list[str],
) -> tuple[str, list[str]] | None:
    """Inspect a filter expression and return a friendly error if needed.

    Returns ``None`` when the filter is empty, when the entity has no known
    fields (so we can't validate), or when every referenced field is known.
    Otherwise returns ``(message, unknown_fields)`` describing the first
    unrecognised reference and any close match.
    """
    if not filter_expr or not known_fields:
        return None
    known_lower = {k.lower() for k in known_fields}
    unknown: list[str] = []
    for ref in extract_field_references(filter_expr):
        if ref.lower() not in known_lower:
            unknown.append(ref)
    if not unknown:
        return None
    first = unknown[0]
    suggestions = suggest_field(first, known_fields)
    hint = ""
    if suggestions:
        hint = f" Did you mean: {', '.join(suggestions)}?"
    msg = (
        f"Filter references unknown field '{first}'.{hint}"
        f" Run 'bcli endpoint fields <entity>' to discover valid field names."
    )
    return msg, unknown
