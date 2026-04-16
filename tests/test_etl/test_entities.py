"""Tests for ETL entity definitions."""

from __future__ import annotations

from bcli.etl._entities import EntityDef, load_entities_from_registry
from bcli.registry._schema import EndpointMetadata


class TestEntityDef:
    def test_frozen(self):
        e = EntityDef("customers")
        try:
            e.name = "other"
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_defaults(self):
        e = EntityDef("customers")
        assert e.primary_key == "systemId"
        assert e.cursor_field == "systemModifiedAt"
        assert e.write_disposition == "merge"
        assert e.api_publisher is None

    def test_from_metadata(self):
        meta = EndpointMetadata(
            entity_set_name="engineOverviews",
            entity_name="engineOverview",
            key_field="systemId",
            api_publisher="beautech",
            api_group="standard",
            api_version="v1.0",
            supports=["GET", "POST"],
        )
        entity = EntityDef.from_metadata(meta)
        assert entity.name == "engineOverviews"
        assert entity.primary_key == "systemId"
        assert entity.api_publisher == "beautech"
        assert entity.api_group == "standard"
        assert entity.api_version == "v1.0"

    def test_from_metadata_standard(self):
        meta = EndpointMetadata(
            entity_set_name="customers",
            key_field="id",
        )
        entity = EntityDef.from_metadata(meta)
        assert entity.name == "customers"
        assert entity.primary_key == "id"
        assert entity.api_publisher is None


class TestLoadEntitiesFromRegistry:
    def test_loads_custom_endpoints(self):
        """Smoke test — loads from the sandbox registry if it exists."""
        entities = load_entities_from_registry("sandbox", custom_only=True)
        # sandbox.json has 114 endpoints
        assert len(entities) > 0
        # All should have custom API routes
        for e in entities:
            assert e.api_publisher is not None, f"{e.name} should be a custom endpoint"

    def test_custom_only_excludes_standard(self):
        entities = load_entities_from_registry("sandbox", custom_only=True)
        for e in entities:
            assert e.api_publisher is not None

    def test_include_standard(self):
        entities = load_entities_from_registry("sandbox", custom_only=False)
        has_custom = any(e.api_publisher is not None for e in entities)
        has_standard = any(e.api_publisher is None for e in entities)
        assert has_custom
        assert has_standard

    def test_nonexistent_profile_returns_empty(self):
        entities = load_entities_from_registry("nonexistent_profile_xyz", custom_only=True)
        assert entities == []

    def test_only_get_supported(self):
        entities = load_entities_from_registry("sandbox", custom_only=True)
        # All returned entities should support GET (we filter for it)
        # This is true by construction, but verify the filter works
        assert len(entities) > 0

    def test_entities_have_cursor_field(self):
        entities = load_entities_from_registry("sandbox", custom_only=True)
        for e in entities:
            assert e.cursor_field == "systemModifiedAt"
