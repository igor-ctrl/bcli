"""Tests for endpoint importers."""

import json
import tempfile
from pathlib import Path

from bcli.registry._importers import import_from_json, import_from_postman


def test_import_from_postman_real_collection():
    """Test against the real sample Postman collection."""
    postman_file = Path("/Users/igor/Projects/Fivetran/fivetran_bc_api.postman_collection.json")
    if not postman_file.is_file():
        import pytest
        pytest.skip("Postman collection not available")

    endpoints = import_from_postman(postman_file)
    assert len(endpoints) > 80  # Should be ~92

    # Check that we got entities from all three groups
    groups = {f"{e.api_publisher}/{e.api_group}/{e.api_version}" for e in endpoints}
    assert "acme/finance/v1.5" in groups
    assert "acme/technical/v1.5" in groups
    assert "acme/standard/v1.0" in groups

    # Check a specific entity
    names = {e.entity_set_name for e in endpoints}
    assert "glAccounts" in names or "vendors" in names


def test_import_from_json_bcmcp_format():
    """Test against the real bcmcp endpoint metadata."""
    json_file = Path("/Users/igor/Projects/bcmcp/API_Endpoint_Metadata.json")
    if not json_file.is_file():
        import pytest
        pytest.skip("bcmcp metadata not available")

    endpoints = import_from_json(json_file)
    assert len(endpoints) > 80

    # Check structure
    for ep in endpoints:
        assert ep.entity_set_name
        assert ep.api_publisher
        assert ep.api_group
        assert ep.api_version


def test_import_from_json_bcli_format():
    """Test bcli-native format."""
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
