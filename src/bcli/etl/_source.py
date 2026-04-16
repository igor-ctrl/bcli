"""dlt source wrapping bcli's AsyncBCClient for Business Central extraction."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from bcli.etl._entities import EntityDef, load_entities_from_registry

try:
    import dlt
except ImportError:
    raise ImportError(
        "dlt is required for ETL features. Install it: pip install 'bcli[etl]'"
    )


def _make_resource(entity: EntityDef, profile: str, full_refresh: bool = False):
    """Create a dlt resource for a single BC entity.

    Extracts from ALL companies in the BC environment, matching Fivetran's
    behavior of cycling through every entity across every company.
    """

    @dlt.resource(
        name=entity.name,
        primary_key=entity.primary_key,
        write_disposition="replace" if full_refresh else entity.write_disposition,
    )
    def _extract(
        modified=dlt.sources.incremental(
            entity.cursor_field,
            initial_value=None,
        ),
    ):
        # First run: initial_value=None → no filter, extracts EVERYTHING
        # Subsequent runs: cursor has a value → filters by systemModifiedAt
        since = None if (full_refresh or modified.start_value is None) else modified.start_value
        yield from _run_async_extract(entity, profile, since)

    return _extract


def _run_async_extract(
    entity: EntityDef, profile: str, since: str | None
) -> list[list[dict[str, Any]]]:
    """Run the async extraction synchronously and return all pages."""
    return asyncio.run(_async_extract_all_companies(entity, profile, since))


def _stamp_fivetran_fields(page: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inject _fivetran_synced and _fivetran_deleted into each record.

    Fivetran adds these to every synced table. dbt models reference them,
    so the backup must include them for seamless failover.
    """
    synced_at = datetime.now(timezone.utc).isoformat()
    return [
        {**record, "_fivetran_synced": synced_at, "_fivetran_deleted": False}
        for record in page
    ]


async def _async_extract_all_companies(
    entity: EntityDef, profile: str, since: str | None
) -> list[list[dict[str, Any]]]:
    """Extract all pages from a BC entity across ALL companies."""
    from bcli import AsyncBCClient

    all_pages: list[list[dict[str, Any]]] = []

    async with AsyncBCClient(profile=profile) as client:
        # Get all companies in the environment
        companies = await client.list_companies()

        for company in companies:
            company_id = company.get("id", "")
            # Swap company_id on the profile for this iteration
            original_company_id = client._profile.company_id
            client._profile.company_id = company_id

            try:
                pages = await _extract_pages(client, entity, since, company_id)
                all_pages.extend(pages)
            finally:
                client._profile.company_id = original_company_id

    return all_pages


async def _async_extract(
    entity: EntityDef, profile: str, since: str | None
) -> list[list[dict[str, Any]]]:
    """Extract all pages from a BC entity for the default company only."""
    from bcli import AsyncBCClient

    async with AsyncBCClient(profile=profile) as client:
        return await _extract_pages(client, entity, since, None)


async def _extract_pages(
    client: Any, entity: EntityDef, since: str | None, company_id: str | None,
) -> list[list[dict[str, Any]]]:
    """Extract paginated data from BC for a single entity/company."""
    pages: list[list[dict[str, Any]]] = []

    q = client.query(entity.name).orderby(f"{entity.cursor_field} asc")

    if entity.api_publisher:
        q = q.route(entity.api_publisher, entity.api_group or "", entity.api_version or "")

    if since:
        q = q.filter(f"{entity.cursor_field} gt {since}")

    async for page in await q.pages():
        stamped = _stamp_fivetran_fields(page)
        if company_id:
            stamped = [{**r, "company_id": company_id} for r in stamped]
        pages.append(stamped)

    return pages


@dlt.source
def business_central(
    profile: str = "default",
    entities: list[str] | None = None,
    full_refresh: bool = False,
    include_standard: bool = False,
):
    """dlt source that extracts Business Central data via the bcli SDK.

    Extracts from ALL companies in the BC environment for each entity,
    matching Fivetran's multi-company sync behavior.

    Args:
        profile: bcli connection profile name.
        entities: Entity names to extract (default: all from registry).
        full_refresh: Ignore incremental cursor and reload everything.
        include_standard: Also include standard v2.0 entities.

    Returns:
        List of dlt resources, one per entity.
    """
    all_entities = load_entities_from_registry(
        profile, custom_only=not include_standard
    )

    if entities is not None:
        name_set = set(entities)
        available = {e.name for e in all_entities}
        unknown = name_set - available
        if unknown:
            raise ValueError(
                f"Unknown entities: {unknown}. "
                f"Available: {sorted(available)}"
            )
        all_entities = [e for e in all_entities if e.name in name_set]

    return [_make_resource(e, profile, full_refresh) for e in all_entities]
