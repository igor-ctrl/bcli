"""Boundary check for saved-query YAML that arrives from packs and bundles.

Query, batch and argument names are copied into shell lines and
frontmatter by ``bcli skill install``, so untrusted sources may only
carry identifier-ish names. Mirrors ``skill_init_cmd._SAFE_NAME_RE``.
"""

from __future__ import annotations

import re
from typing import Any

SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def unsafe_names(name: Any, body: Any) -> list[str]:
    """Return every name under one saved query/batch that is not identifier-ish.

    Checks ``name`` plus ``args[].name`` and the ``params:`` keys, the
    two places ``skill install`` turns into arguments.
    """
    names = [name]
    if isinstance(body, dict):
        args = body.get("args")
        if isinstance(args, list):
            names += [a["name"] for a in args if isinstance(a, dict) and "name" in a]
        params = body.get("params")
        if isinstance(params, dict):
            names += list(params)
    return [str(n) for n in names if SAFE_NAME_RE.fullmatch(str(n)) is None]
