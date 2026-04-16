"""Tests for the bcli-aware bridge layer."""

from __future__ import annotations

import pytest

pytest.importorskip("dlt")

from bcli.etl._bridge import (
    bcli_profile,
    load_entities_from_bcli_registry,
)
from bcli.etl._generic import EntityDef


class TestLoadEntitiesFromBcliRegistry:
    def test_loads_custom_endpoints(self):
        """Smoke test — loads from sandbox registry if it exists on this machine."""
        entities = load_entities_from_bcli_registry("sandbox", custom_only=True)
        if not entities:
            pytest.skip("No sandbox registry present — developer-local smoke test")
        assert all(isinstance(e, EntityDef) for e in entities)
        # Custom-only → all should have a publisher
        for e in entities:
            assert e.api_publisher is not None

    def test_nonexistent_profile_returns_empty(self):
        entities = load_entities_from_bcli_registry("nonexistent_xyz_profile", custom_only=True)
        assert entities == []

    def test_entities_have_systemModifiedAt_cursor(self):
        entities = load_entities_from_bcli_registry("sandbox", custom_only=True)
        if not entities:
            pytest.skip("No sandbox registry")
        for e in entities:
            assert e.cursor_field == "systemModifiedAt"


class TestBcliProfileSource:
    def test_requires_valid_profile(self):
        with pytest.raises(Exception):
            bcli_profile("nonexistent_xyz_profile")

    def test_unknown_entity_raises(self):
        # Use a real profile if available, otherwise skip
        entities = load_entities_from_bcli_registry("sandbox", custom_only=True)
        if not entities:
            pytest.skip("No sandbox registry")
        with pytest.raises(ValueError, match="Unknown entities"):
            bcli_profile("sandbox", entities=["definitely_not_an_entity_xyz"])

    def test_filters_to_subset(self):
        entities = load_entities_from_bcli_registry("sandbox", custom_only=True)
        if not entities:
            pytest.skip("No sandbox registry")
        name = entities[0].name
        source = bcli_profile("sandbox", entities=[name])
        assert name in {r.name for r in source.resources.values()}
