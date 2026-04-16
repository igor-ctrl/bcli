"""Optional field-injection stampers for the BC ETL source.

Stampers are post-processing functions applied to each page of records
before dlt ingests them. They add metadata columns (sync timestamps,
source identifiers, soft-delete flags) for downstream compatibility.

This module is part of the generic layer and must not import from bcli.*.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

Stamper = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def fivetran_stamper() -> Stamper:
    """Add Fivetran-compatible audit columns to every record.

    Adds:
    - ``_fivetran_synced``: ISO-8601 UTC timestamp of when the record was synced.
    - ``_fivetran_deleted``: always ``False`` (soft-delete flag; BC doesn't
      expose deletions, so downstream models should filter on this anyway).

    Use this when migrating from or coexisting with Fivetran. Downstream
    dbt models that reference these columns keep working unchanged.
    """

    def _stamp(page: list[dict[str, Any]]) -> list[dict[str, Any]]:
        synced_at = datetime.now(timezone.utc).isoformat()
        return [
            {**record, "_fivetran_synced": synced_at, "_fivetran_deleted": False}
            for record in page
        ]

    return _stamp


def audit_stamper(source_name: str) -> Stamper:
    """Add a generic audit trail (`_synced_at`, `_source`) to every record.

    Use this for new pipelines not tied to Fivetran conventions.
    """

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
