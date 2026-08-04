"""Saved-query catalog loading — from a YAML file or an already-parsed mapping.

A catalog is the ``queries:`` block of a bundle's YAML file: a mapping of
query name -> spec (see :mod:`bcli_cli.commands.query_cmd` for the full
per-query schema). Loading never talks to Business Central; it only parses
YAML and checks the two structural invariants a bad catalog can violate —
``queries`` must be a mapping, and no query may be named after a ``bcli q``
sub-verb (``list``, ``search``, ``find``, ``info``, ``run``), since a query
with one of those names would be unreachable except via ``bcli q run
<name>``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from bcli.queries._errors import QueryCatalogError

# Reserved names that `bcli q` sub-verb dispatch consumes. A query whose name
# lives here is unreachable except via `bcli q run <name>`, so catalog
# loading hard-errors on a collision — this is a misconfigured bundle, and
# the right place to catch it is at load time, not at dispatch time.
RESERVED_QUERY_NAMES = frozenset({"list", "search", "find", "info", "run"})


def load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    """Load and validate a saved-query catalog from a YAML file.

    Returns ``{}`` if ``path`` doesn't exist — an absent catalog is a normal
    state (a profile with no saved queries yet), not an error.

    Raises :class:`QueryCatalogError` if the file fails to parse, or if the
    parsed structure fails :func:`load_catalog_from_mapping`'s checks.
    """
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise QueryCatalogError(f"Failed to parse {path}: {e}") from e
    return load_catalog_from_mapping(raw, source=path)


def load_catalog_from_mapping(
    raw: Mapping[str, Any] | None,
    *,
    source: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Validate an already-parsed catalog mapping (the ``{"queries": {...}}`` shape).

    Use this when the YAML has already been parsed elsewhere (a config
    service, a test fixture) and only the structural checks are needed.
    ``source`` is used purely to make error messages actionable — pass the
    file path (or any label) it came from, when known.
    """
    label = str(source) if source is not None else "saved-query catalog"
    queries = (raw or {}).get("queries", {})
    if not isinstance(queries, dict):
        raise QueryCatalogError(f"{label}: 'queries' must be a mapping.")

    collisions = sorted(set(queries) & RESERVED_QUERY_NAMES)
    if collisions:
        raise QueryCatalogError(
            f"{label}: reserved query names used: {', '.join(collisions)}.\n"
            f"These names collide with `bcli q` sub-verbs. Rename the queries "
            f"or invoke them via `bcli q run <name>`. "
            f"Reserved: {sorted(RESERVED_QUERY_NAMES)}"
        )

    return queries


def resolve_alias(catalog: Mapping[str, Mapping[str, Any]], term: str) -> str | None:
    """Return the canonical query name when ``term`` matches a declared alias."""
    term_lower = term.lower()
    for q_name, body in catalog.items():
        aliases = body.get("aliases") or []
        if not isinstance(aliases, (list, tuple)):
            continue
        if any(str(a).lower() == term_lower for a in aliases):
            return q_name
    return None


def resolve_query_name(catalog: Mapping[str, Mapping[str, Any]], name: str) -> str | None:
    """Return the canonical name for ``name`` — itself, or the query an alias points to.

    ``None`` means neither a direct name nor an alias matched anything in
    ``catalog``.
    """
    if name in catalog:
        return name
    return resolve_alias(catalog, name)
