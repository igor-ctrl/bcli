"""Tests for the bcli-aware bridge layer."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("dlt")

import bcli.registry._registry as registry_mod
from bcli.errors import ConfigError
from bcli.etl._bridge import (
    bcli_profile,
    load_entities_from_bcli_registry,
)
from bcli.etl._generic import EntityDef


@pytest.fixture(autouse=True)
def _hermetic_config(tmp_path, monkeypatch):
    """Two profiles with one custom endpoint each; never the developer's real config.

    ``sandbox`` sees standard v2.0 plus its custom route; ``locked`` has
    ``disable_standard_api = true``.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[defaults]\nprofile = "sandbox"\n\n'
        '[profiles.sandbox]\ntenant_id = "t1"\nenvironment = "Sandbox"\n\n'
        '[profiles.locked]\ntenant_id = "t1"\nenvironment = "Sandbox"\n'
        "disable_standard_api = true\n"
    )
    monkeypatch.setattr("bcli.config._loader.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._loader._find_project_config", lambda: None)
    registries = tmp_path / "registries"
    registries.mkdir()
    monkeypatch.setattr(registry_mod, "REGISTRIES_DIR", registries)
    endpoint = {
        "entity_set_name": "engines",
        "api_publisher": "beautech", "api_group": "technical", "api_version": "v1.0",
    }
    for name in ("sandbox", "locked"):
        (registries / f"{name}.json").write_text(json.dumps({"endpoints": [endpoint]}))


class TestLoadEntitiesFromBcliRegistry:
    def test_loads_custom_endpoints(self):
        entities = load_entities_from_bcli_registry("sandbox", custom_only=True)
        assert [e.name for e in entities] == ["engines"]
        assert all(isinstance(e, EntityDef) for e in entities)
        # Custom-only → all should have a publisher
        for e in entities:
            assert e.api_publisher is not None

    def test_nonexistent_profile_raises(self):
        # The registry is now the profile's own (via AsyncBCClient), so an
        # unknown profile fails loudly instead of yielding an empty list.
        with pytest.raises(ConfigError, match="nonexistent_xyz_profile"):
            load_entities_from_bcli_registry("nonexistent_xyz_profile", custom_only=True)

    def test_entities_have_systemModifiedAt_cursor(self):
        entities = load_entities_from_bcli_registry("sandbox", custom_only=True)
        assert entities
        for e in entities:
            assert e.cursor_field == "systemModifiedAt"

    def test_standard_included_when_not_custom_only(self):
        entities = load_entities_from_bcli_registry("sandbox", custom_only=False)
        assert len(entities) > 1
        assert any(e.api_publisher is None for e in entities)

    def test_disable_standard_api_yields_only_custom(self):
        entities = load_entities_from_bcli_registry("locked", custom_only=False)
        assert [e.name for e in entities] == ["engines"]


class TestBcliProfileSource:
    def test_requires_valid_profile(self):
        with pytest.raises(ConfigError):
            bcli_profile("nonexistent_xyz_profile")

    def test_unknown_entity_raises(self):
        with pytest.raises(ValueError, match="Unknown entities"):
            bcli_profile("sandbox", entities=["definitely_not_an_entity_xyz"])

    def test_filters_to_subset(self):
        source = bcli_profile("sandbox", entities=["engines"])
        assert "engines" in {r.name for r in source.resources.values()}
