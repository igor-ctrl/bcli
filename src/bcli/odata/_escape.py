"""OData v4 literal escaping helpers.

Saved queries and other places that interpolate user-supplied values into a
``$filter`` string need to escape single quotes so a value like
``193208' or 1 eq 1--`` cannot break out of the string literal it's pasted
into. This module centralises that rule so both the saved-query layer and any
future call-sites stay consistent.

The escape is intentionally minimal — only the OData v4 ``Edm.String`` literal
rule (``'`` → ``''``). Other literal forms (numbers, GUIDs, dates) are
expressed unquoted in OData and don't need escaping; per-parameter type
validation in the saved-query layer is what keeps them well-formed.
"""

from __future__ import annotations


def escape_odata_string(value: str) -> str:
    """Escape an OData v4 string literal for safe inlining inside ``'...'``.

    OData rule: a single quote inside a string literal is doubled. Everything
    else passes through unchanged, since OData v4 does not interpret backslash
    escapes the way SQL or shells do.

    >>> escape_odata_string("Acme")
    'Acme'
    >>> escape_odata_string("O'Brien")
    "O''Brien"
    >>> escape_odata_string("193208' or 1 eq 1--")
    "193208'' or 1 eq 1--"
    """
    return value.replace("'", "''")
