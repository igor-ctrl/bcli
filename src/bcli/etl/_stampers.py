"""Optional field-injection stampers for the BC ETL source.

Stampers are post-processing functions applied to each page of records
before dlt ingests them. They add metadata columns (sync timestamps,
source identifiers, etc.) for downstream compatibility.

This package ships only vendor-neutral stampers. Vendor-specific audit
conventions live in downstream packages and register through the
``bcli.etl.stampers`` entry-point group — see
:mod:`bcli.etl._stamper_factory`.

This module is part of the generic layer and must not import from bcli.*.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

Stamper = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def audit_stamper(source_name: str) -> Stamper:
    """Add a generic audit trail (`_synced_at`, `_source`) to every record."""

    def _stamp(page: list[dict[str, Any]]) -> list[dict[str, Any]]:
        synced_at = datetime.now(timezone.utc).isoformat()
        return [
            {**record, "_synced_at": synced_at, "_source": source_name}
            for record in page
        ]

    return _stamp


def company_id_stamper(company_id: str) -> Stamper:
    """Attach a ``company_id`` column to every record.

    Used internally by the multi-company extractor; also exposed so
    downstream users can reuse the same helper if they roll their own loop.
    """

    def _stamp(page: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**record, "company_id": company_id} for record in page]

    return _stamp


def apply_stampers(page: list[dict[str, Any]], stampers: list[Stamper]) -> list[dict[str, Any]]:
    """Apply a list of stampers in order."""
    result = page
    for stamper in stampers:
        result = stamper(result)
    return result
