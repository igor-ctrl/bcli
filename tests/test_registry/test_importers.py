"""Tests for endpoint importers."""

import json
import tempfile
from pathlib import Path

from bcli.registry._importers import import_from_json, import_from_postman

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_import_from_postman_synthetic_collection():
    """Import from the bundled synthetic Postman fixture."""
    postman_file = FIXTURES / "sample_postman_collection.json"
    assert postman_file.is_file(), "fixture missing — expected at tests/fixtures/sample_postman_collection.json"

    endpoints = import_from_postman(postman_file)

    # The fixture has 5 distinct entities across 3 API groups
    assert len(endpoints) == 5

    # All three groups present
    groups = {f"{e.api_publisher}/{e.api_group}/{e.api_version}" for e in endpoints}
    assert "acme/finance/v1.0" in groups
    assert "acme/standard/v1.0" in groups
    assert "acme/technical/v1.0" in groups

    # Specific entities parsed correctly
    names = {e.entity_set_name for e in endpoints}
    assert "glAccounts" in names
    assert "customers" in names
    assert "vendors" in names
    assert "equipmentRecords" in names

    # Methods collected (GL entries endpoint has POST)
    gl_entries = [e for e in endpoints if e.entity_set_name == "glEntries"]
    assert not gl_entries or "POST" in gl_entries[0].supports


def test_import_from_json_with_endpoints_key():
    """Import a synthetic JSON file in bcli-native format."""
    data = {
        "endpoints": [
            {
                "entity_set_name": "glEntries",
                "entity_name": "glEntry",
                "api_publisher": "acme",
                "api_group": "finance",
                "api_version": "v1.0",
                "description": "GL Entry endpoint",
                "supports": ["GET"],
                "key_field": "systemId",
            },
            {
                "entity_set_name": "customers",
                "entity_name": "customer",
                "api_publisher": "acme",
                "api_group": "standard",
                "api_version": "v1.0",
                "description": "Customer endpoint",
                "supports": ["GET", "POST", "PATCH"],
                "key_field": "systemId",
            },
        ]
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        f.flush()
        endpoints = import_from_json(Path(f.name))

    assert len(endpoints) == 2
    for ep in endpoints:
        assert ep.entity_set_name
        assert ep.api_publisher == "acme"
        assert ep.api_group
        assert ep.api_version


def test_import_from_json_bcli_format():
    """Test bcli-native format with minimal fields."""
    data = {
        "endpoints": [
            {
                "entity_set_name": "testEntities",
                "entity_name": "testEntity",
                "api_publisher": "test",
                "api_group": "api",
                "api_version": "v1.0",
                "description": "Test endpoint",
                "supports": ["GET", "POST"],
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        f.flush()
        endpoints = import_from_json(Path(f.name))

    assert len(endpoints) == 1
    assert endpoints[0].entity_set_name == "testEntities"
    assert endpoints[0].api_publisher == "test"
    assert "GET" in endpoints[0].supports
    assert "POST" in endpoints[0].supports
