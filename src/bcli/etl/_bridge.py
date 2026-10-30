"""bcli-aware adapter for the generic ETL source.

Translates bcli-specific concepts (profile, registry, auth provider) into
the generic source's abstractions (AuthProvider, EntityDef list). The
generic source itself never sees a profile; its HTTP goes through bcli's
transport (see ``bcli.etl._client``).
"""

from __future__ import annotations

from typing import Any

from bcli.etl._generic import EntityDef, business_central as _generic_business_central
from bcli.etl._stamper_factory import build_stampers
from bcli.etl._stampers import Stamper


def load_entities_from_bcli_registry(
    profile: str, *, custom_only: bool = True
) -> list[EntityDef]:
    """Translate bcli's EndpointRegistry into a list of EntityDef.

    Only entities supporting GET are included. The registry is the profile's
    own, so ``disable_standard_api`` and the allowed category/endpoint lists
    apply exactly as they do to ``bcli get``.
    """
    from bcli import AsyncBCClient

    return _entities(AsyncBCClient(profile=profile).registry, custom_only)


def _entities(registry: Any, custom_only: bool) -> list[EntityDef]:
    return [
        EntityDef(
            name=ep.entity_set_name,
            primary_key=ep.key_field,
            api_publisher=ep.api_publisher,
            api_group=ep.api_group,
            api_version=ep.api_version,
        )
        for ep in registry.list_all(custom_only=custom_only)
        if "GET" in ep.supports
    ]


def bcli_profile(
    profile: str,
    *,
    entities: list[str] | None = None,
    full_refresh: bool = False,
    multi_company: bool = True,
    include_standard: bool = False,
    stampers: list[str] | None = None,
    extra_stampers: list[Stamper] | None = None,
) -> Any:
    """dlt source using a bcli profile's registry + auth.

    Output is a clean record shape by default — no audit/metadata
    columns. Those come from stampers registered under the
    ``bcli.etl.stampers`` entry-point group and opted into per config
    or via the ``stampers`` argument.

    Args:
        profile: bcli profile name (from ``~/.config/bcli/config.toml``).
        entities: Restrict to these entity names. Default: all custom endpoints.
        full_refresh: Ignore incremental cursor.
        multi_company: Iterate across all companies, adding a ``company_id``
            column to every record.
        include_standard: Include standard v2.0 entities in addition to custom.
        stampers: Entry-point names (group ``bcli.etl.stampers``) to apply.
            Overrides ``[etl] stampers`` config when provided; pass ``[]`` to
            force a clean shape regardless of config. ``None`` (default) reads
            the config list.
        extra_stampers: Programmatic stampers applied after the named ones.

    Returns:
        A dlt source ready to pass to ``pipeline.run(...)``.
    """
    from bcli import AsyncBCClient
    from bcli.config import load_config

    config = load_config()
    client = AsyncBCClient(profile=profile, config=config)
    environment = client.profile.environment

    # Translate registry → EntityDef list
    all_entities = _entities(client.registry, custom_only=not include_standard)
    if entities is not None:
        name_set = set(entities)
        available = {e.name for e in all_entities}
        unknown = name_set - available
        if unknown:
            raise ValueError(
                f"Unknown entities: {unknown}. Available: {sorted(available)}"
            )
        all_entities = [e for e in all_entities if e.name in name_set]

    # Build stampers from registered plugins. Explicit `stampers=` overrides
    # config; otherwise read the opt-in `[etl] stampers` list.
    names = stampers if stampers is not None else list(config.etl.stampers)
    resolved_stampers: list[Stamper] = build_stampers(names)
    if extra_stampers:
        resolved_stampers.extend(extra_stampers)

    # bcli's own provider: one instance, so its token cache and refresh
    # carry across every page and company.
    return _generic_business_central(
        auth=client.auth,
        environment=environment,
        entities=all_entities,
        multi_company=multi_company,
        stampers=resolved_stampers,
        full_refresh=full_refresh,
    )
