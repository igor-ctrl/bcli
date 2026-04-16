"""bcli-aware adapter for the generic ETL source.

This is the ONLY module in bcli.etl that may import from bcli.*.
It translates bcli-specific concepts (profile, registry, token cache) into
the generic source's abstractions (AuthProvider, EntityDef list).
"""

from __future__ import annotations

from typing import Any

from bcli.etl._auth import StaticTokenAuth
from bcli.etl._generic import EntityDef, business_central as _generic_business_central
from bcli.etl._stampers import Stamper, fivetran_stamper


def load_entities_from_bcli_registry(
    profile: str, *, custom_only: bool = True
) -> list[EntityDef]:
    """Translate bcli's EndpointRegistry into a list of EntityDef.

    Only entities supporting GET are included.
    """
    from bcli.registry._registry import EndpointRegistry

    registry = EndpointRegistry(profile_name=profile)
    endpoints = registry.list_all(custom_only=custom_only)

    return [
        EntityDef(
            name=ep.entity_set_name,
            primary_key=ep.key_field,
            api_publisher=ep.api_publisher,
            api_group=ep.api_group,
            api_version=ep.api_version,
        )
        for ep in endpoints
        if "GET" in ep.supports
    ]


def _build_token_provider(profile: str):
    """Return an async callable that yields a fresh bearer token via bcli."""

    async def _token() -> str:
        from bcli import AsyncBCClient

        async with AsyncBCClient(profile=profile) as client:
            transport = client._ensure_transport()
            # bcli's transport handles caching + refresh internally
            return await transport._auth.get_access_token()

    return _token


def bcli_profile(
    profile: str,
    *,
    entities: list[str] | None = None,
    full_refresh: bool = False,
    multi_company: bool = True,
    fivetran_compat: bool = True,
    include_standard: bool = False,
    extra_stampers: list[Stamper] | None = None,
) -> Any:
    """dlt source using a bcli profile's registry + auth.

    Defaults match Fivetran parity: multi-company on, Fivetran
    audit columns on. Pass ``fivetran_compat=False`` for a cleaner record shape
    in new downstream models.

    Args:
        profile: bcli profile name (from ``~/.config/bcli/config.toml``).
        entities: Restrict to these entity names. Default: all custom endpoints.
        full_refresh: Ignore incremental cursor.
        multi_company: Iterate across all companies (Fivetran behavior).
        fivetran_compat: Add ``_fivetran_synced`` / ``_fivetran_deleted`` columns.
        include_standard: Include standard v2.0 entities in addition to custom.
        extra_stampers: Optional extra stampers applied after the built-ins.

    Returns:
        A dlt source ready to pass to ``pipeline.run(...)``.
    """
    # Resolve environment from the profile
    from bcli.config import load_config

    config = load_config()
    bc_profile = config.get_profile(profile)
    environment = bc_profile.environment

    # Translate registry → EntityDef list
    all_entities = load_entities_from_bcli_registry(
        profile, custom_only=not include_standard
    )
    if entities is not None:
        name_set = set(entities)
        available = {e.name for e in all_entities}
        unknown = name_set - available
        if unknown:
            raise ValueError(
                f"Unknown entities: {unknown}. Available: {sorted(available)}"
            )
        all_entities = [e for e in all_entities if e.name in name_set]

    # Build stampers list
    stampers: list[Stamper] = []
    if fivetran_compat:
        stampers.append(fivetran_stamper())
    if extra_stampers:
        stampers.extend(extra_stampers)

    # Wrap bcli's auth as an AuthProvider
    auth = StaticTokenAuth(_build_token_provider(profile))

    return _generic_business_central(
        auth=auth,
        environment=environment,
        entities=all_entities,
        multi_company=multi_company,
        stampers=stampers,
        full_refresh=full_refresh,
    )
