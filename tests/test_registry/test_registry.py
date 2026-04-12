"""Tests for endpoint registry."""

from bcapi.registry._registry import EndpointRegistry
from bcapi.registry._schema import EndpointMetadata


def test_standard_registry_loads():
    registry = EndpointRegistry()
    assert registry.standard_count > 0


def test_standard_lookup():
    registry = EndpointRegistry()
    ep = registry.get("customers")
    assert ep is not None
    assert ep.entity_set_name == "customers"
    assert not ep.is_custom
    assert "GET" in ep.supports


def test_case_insensitive_lookup():
    registry = EndpointRegistry()
    ep = registry.get("Customers")
    assert ep is not None
    assert ep.entity_set_name == "customers"


def test_not_found_returns_none():
    registry = EndpointRegistry()
    assert registry.get("nonexistentEntity") is None


def test_resolve_raises_on_not_found():
    import pytest
    from bcapi.errors import RegistryError

    registry = EndpointRegistry()
    with pytest.raises(RegistryError, match="not found"):
        registry.resolve("nonexistentEntity")


def test_search():
    registry = EndpointRegistry()
    results = registry.search("vendor")
    assert len(results) > 0
    names = [r.entity_set_name for r in results]
    assert "vendors" in names


def test_list_all():
    registry = EndpointRegistry()
    all_endpoints = registry.list_all()
    assert len(all_endpoints) == registry.standard_count

    standard_only = registry.list_all(standard_only=True)
    assert len(standard_only) == registry.standard_count


def test_endpoint_metadata_is_custom():
    standard = EndpointMetadata(entity_set_name="customers")
    assert not standard.is_custom

    custom = EndpointMetadata(
        entity_set_name="engineOverviews",
        api_publisher="beautech",
        api_group="technical",
        api_version="v1.5",
    )
    assert custom.is_custom
    assert "beautech" in custom.route_display
