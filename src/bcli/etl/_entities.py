"""Entity definitions for ETL extraction — registry-driven."""

from __future__ import annotations

from dataclasses import dataclass

from bcli.registry._schema import EndpointMetadata


@dataclass(frozen=True)
class EntityDef:
    """A Business Central entity available for ETL extraction."""

    name: str
    primary_key: str = "systemId"
    cursor_field: str = "systemModifiedAt"
    write_disposition: str = "merge"
    # Route info for custom APIs
    api_publisher: str | None = None
    api_group: str | None = None
    api_version: str | None = None

    @staticmethod
    def from_metadata(meta: EndpointMetadata) -> EntityDef:
        """Create an EntityDef from a registry EndpointMetadata."""
        return EntityDef(
            name=meta.entity_set_name,
            primary_key=meta.key_field,
            api_publisher=meta.api_publisher,
            api_group=meta.api_group,
            api_version=meta.api_version,
        )


def load_entities_from_registry(
    profile: str,
    *,
    custom_only: bool = True,
) -> list[EntityDef]:
    """Load ETL entity definitions from the bcli endpoint registry.

    By default loads only custom (non-standard v2.0) endpoints, since those
    are the ones not covered by Fivetran or other managed sync tools.
    """
    from bcli.registry._registry import EndpointRegistry

    registry = EndpointRegistry(profile_name=profile)

    if custom_only:
        endpoints = registry.list_all(custom_only=True)
    else:
        endpoints = registry.list_all()

    # Only include entities that support GET
    return [
        EntityDef.from_metadata(meta)
        for meta in endpoints
        if "GET" in meta.supports
    ]
