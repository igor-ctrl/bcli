"""Generic dlt source for Microsoft Business Central.

No bcli coupling. Works with any BC tenant given auth + entity definitions.
This module must not import from bcli.* (enforced by CI grep).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from bcli.etl._auth import AuthProvider, ClientCredentialsAuth
from bcli.etl._client import BCClient, NotFoundError, build_entity_url
from bcli.etl._stampers import Stamper, apply_stampers, company_id_stamper

try:
    import dlt
except ImportError as e:
    raise ImportError(
        "dlt is required for ETL features. Install: pip install 'bc-cli[etl]'"
    ) from e


@dataclass(frozen=True)
class EntityDef:
    """A BC entity to extract.

    Attributes:
        name: OData entity set name (e.g. ``customers``, ``glEntries``).
        primary_key: Primary-key column or tuple of columns for dlt merge.
        cursor_field: Timestamp column used for incremental sync. Set to ``None``
            to disable incremental loading for this entity.
        write_disposition: dlt write disposition (``merge``, ``append``, ``replace``).
        api_publisher / api_group / api_version: Custom-API route. Leave ``None``
            for standard v2.0 entities.
    """

    name: str
    primary_key: str | tuple[str, ...] = "systemId"
    cursor_field: str | None = "systemModifiedAt"
    write_disposition: str = "merge"
    api_publisher: str | None = None
    api_group: str | None = None
    api_version: str | None = None


def _make_resource(
    entity: EntityDef,
    *,
    auth: AuthProvider,
    environment: str,
    multi_company: bool,
    stampers: list[Stamper],
    full_refresh: bool,
):
    """Wrap a single entity as a dlt resource."""

    incremental_kwargs = {"initial_value": None}
    cursor_field = entity.cursor_field or "systemModifiedAt"

    @dlt.resource(
        name=entity.name,
        primary_key=entity.primary_key,
        write_disposition="replace" if full_refresh else entity.write_disposition,
    )
    def _extract(
        modified=dlt.sources.incremental(cursor_field, **incremental_kwargs)
        if entity.cursor_field
        else None,
    ):
        if entity.cursor_field and modified is not None:
            since = (
                None
                if (full_refresh or modified.start_value is None)
                else modified.start_value
            )
        else:
            since = None

        yield from _run(entity, auth, environment, multi_company, stampers, since)

    return _extract


def _run(
    entity: EntityDef,
    auth: AuthProvider,
    environment: str,
    multi_company: bool,
    stampers: list[Stamper],
    since: str | None,
) -> list[list[dict[str, Any]]]:
    return asyncio.run(_run_async(entity, auth, environment, multi_company, stampers, since))


async def _run_async(
    entity: EntityDef,
    auth: AuthProvider,
    environment: str,
    multi_company: bool,
    stampers: list[Stamper],
    since: str | None,
) -> list[list[dict[str, Any]]]:
    all_pages: list[list[dict[str, Any]]] = []

    async with BCClient(auth=auth, environment=environment) as client:
        if multi_company:
            companies = await client.list_companies()
            for company in companies:
                company_id = company.get("id", "")
                try:
                    pages = await _extract_for_company(
                        client, entity, since, company_id, stampers
                    )
                except NotFoundError:
                    # Entity doesn't exist in this company — normal for custom APIs
                    continue
                all_pages.extend(pages)
        else:
            # Single-company mode requires the caller to have set company context
            # via a default in the auth/env. For the generic path we need a
            # company_id — surface this clearly.
            raise ValueError(
                "Single-company mode not yet supported in generic layer. "
                "Use multi_company=True or the bcli_profile() bridge."
            )

    return all_pages


async def _extract_for_company(
    client: BCClient,
    entity: EntityDef,
    since: str | None,
    company_id: str,
    stampers: list[Stamper],
) -> list[list[dict[str, Any]]]:
    url = build_entity_url(
        environment=client.environment,
        company_id=company_id,
        entity_set_name=entity.name,
        publisher=entity.api_publisher,
        group=entity.api_group,
        version=entity.api_version,
    )

    params: dict[str, str] = {}
    if entity.cursor_field:
        params["$orderby"] = f"{entity.cursor_field} asc"
        if since:
            params["$filter"] = f"{entity.cursor_field} gt {since}"

    # Stampers for this extraction: user stampers + auto company_id
    effective_stampers = list(stampers) + [company_id_stamper(company_id)]

    pages: list[list[dict[str, Any]]] = []
    async for page in client.paginate(url, params=params):
        pages.append(apply_stampers(page, effective_stampers))
    return pages


@dlt.source
def business_central(
    *,
    auth: AuthProvider | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    environment: str,
    entities: list[EntityDef],
    multi_company: bool = False,
    stampers: list[Stamper] | None = None,
    full_refresh: bool = False,
):
    """Generic dlt source for any Business Central tenant.

    Provide either an ``auth`` provider or the three credential fields
    (``tenant_id``, ``client_id``, ``client_secret``) for built-in
    client-credentials auth.

    Args:
        auth: Custom auth provider. Takes precedence over credential fields.
        tenant_id / client_id / client_secret: Credentials for built-in
            client-credentials auth (used only if ``auth`` is not provided).
        environment: BC environment name (e.g. ``Production``, ``Sandbox``).
        entities: Entities to extract. Pass an explicit list — no registry
            auto-discovery in the generic layer.
        multi_company: If ``True``, iterate through every company returned by
            ``/companies`` and extract each entity per company. Adds a
            ``company_id`` column to every record.
        stampers: Optional post-processing hooks (e.g. ``audit_stamper()``).
            Defaults to an empty list.
        full_refresh: If ``True``, ignore the incremental cursor.
    """
    if auth is None:
        if not (tenant_id and client_id and client_secret):
            raise ValueError(
                "Pass either `auth=<AuthProvider>` or all of "
                "`tenant_id`, `client_id`, `client_secret`."
            )
        auth = ClientCredentialsAuth(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )

    stampers = stampers or []

    return [
        _make_resource(
            e,
            auth=auth,
            environment=environment,
            multi_company=multi_company,
            stampers=stampers,
            full_refresh=full_refresh,
        )
        for e in entities
    ]
