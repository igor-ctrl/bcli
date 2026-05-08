"""Discoverability layer over saved queries.

Pure functions. Takes the dict-shaped queries already on disk and gives
back ranked / filtered views for ``bcli q list`` / ``q search`` / ``q
info``. No embeddings, no Redis — substring + tag/alias scoring, which the
codex consult correctly identified as 80%-of-the-value-without-Redis for a
50-100 query library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class QueryEntry:
    """One saved query, normalized for search/listing.

    Built from the raw dict the YAML loader returns. Keeping a normalized
    view lets the search code stay pure (it never re-parses YAML or pokes
    at None vs. empty list).
    """

    name: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    owner: str = ""
    freshness: str = ""
    examples: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    endpoint: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, name: str, raw: dict[str, Any]) -> "QueryEntry":
        def _as_tuple(value: Any) -> tuple[str, ...]:
            if value is None:
                return ()
            if isinstance(value, (list, tuple)):
                return tuple(str(v) for v in value)
            return (str(value),)

        return cls(
            name=name,
            description=str(raw.get("description") or ""),
            aliases=_as_tuple(raw.get("aliases")),
            tags=_as_tuple(raw.get("tags")),
            owner=str(raw.get("owner") or ""),
            freshness=str(raw.get("freshness") or ""),
            examples=_as_tuple(raw.get("examples")),
            related=_as_tuple(raw.get("related")),
            endpoint=str(raw.get("endpoint") or ""),
            params=dict(raw.get("params") or {}),
        )


def normalize_queries(raw_queries: dict[str, dict[str, Any]]) -> list[QueryEntry]:
    """Convert a YAML-loaded mapping into sorted entries."""
    return sorted(
        (QueryEntry.from_raw(name, body) for name, body in raw_queries.items()),
        key=lambda q: q.name,
    )


def filter_entries(
    entries: Iterable[QueryEntry],
    *,
    tag: str | None = None,
    owner: str | None = None,
    freshness: str | None = None,
) -> list[QueryEntry]:
    """Filter entries by simple metadata predicates. Case-insensitive."""
    out = list(entries)
    if tag:
        tag_lower = tag.lower()
        out = [q for q in out if any(t.lower() == tag_lower for t in q.tags)]
    if owner:
        owner_lower = owner.lower()
        out = [q for q in out if q.owner.lower() == owner_lower]
    if freshness:
        fresh_lower = freshness.lower()
        out = [q for q in out if q.freshness.lower() == fresh_lower]
    return out


def search_entries(
    entries: Iterable[QueryEntry],
    query: str,
    *,
    score_floor: int = 30,
) -> list[tuple[int, QueryEntry]]:
    """Rank entries by a composite score. Returns ``[(score, entry), ...]``.

    Scoring is intentionally chunky — the goal is "this is the query you
    want" vs "no, here are some near-misses," not a continuous relevance
    function. Cutoff at ``score_floor`` so a query over a wildly-unrelated
    string returns nothing rather than the whole catalog.
    """
    q = query.strip().lower()
    if not q:
        return []

    scored: list[tuple[int, QueryEntry]] = []
    for entry in entries:
        score = _score_entry(entry, q)
        if score >= score_floor:
            scored.append((score, entry))

    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return scored


def _score_entry(entry: QueryEntry, q: str) -> int:
    score = 0
    name_lower = entry.name.lower()
    if name_lower == q:
        score = max(score, 100)
    elif name_lower.startswith(q):
        score = max(score, 90)
    elif q in name_lower:
        score = max(score, 75)

    for alias in entry.aliases:
        alias_lower = alias.lower()
        if alias_lower == q:
            score = max(score, 95)
        elif q in alias_lower:
            score = max(score, 70)

    for tag in entry.tags:
        if q == tag.lower():
            score = max(score, 60)
        elif q in tag.lower():
            score = max(score, 50)

    desc_lower = entry.description.lower()
    if desc_lower:
        # Token overlap: any non-trivial query token sitting inside the
        # description bumps the score, but description matches always rank
        # below name/alias hits.
        for token in q.split():
            if len(token) >= 3 and token in desc_lower:
                score = max(score, 45)

    for example in entry.examples:
        if q in example.lower():
            score = max(score, 40)

    if q in entry.endpoint.lower():
        score = max(score, 35)

    return score
