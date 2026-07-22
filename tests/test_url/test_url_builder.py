"""Tests for URL builder."""

import pytest

from bcli._url import build_companies_url, build_metadata_url, build_url


def test_standard_v2_url():
    url = build_url(
        environment="Production",
        company_id="abc-123",
        entity_set_name="customers",
    )
    assert "api/v2.0" in url
    assert "Production" in url
    assert "companies(abc-123)" in url
    assert url.endswith("/customers")


def test_custom_api_url():
    url = build_url(
        environment="SBEnvOct25T",
        company_id="abc-123",
        entity_set_name="engineOverviews",
        publisher="contoso",
        group="technical",
        version="v1.5",
    )
    assert "api/contoso/technical/v1.5" in url
    assert "SBEnvOct25T" in url
    assert url.endswith("/engineOverviews")


def test_url_with_record_id():
    url = build_url(
        environment="Production",
        company_id="abc-123",
        entity_set_name="customers",
        record_id="record-456",
    )
    assert url.endswith("/customers(record-456)")


def test_companies_url():
    url = build_companies_url(environment="Production")
    assert "api/v2.0/companies" in url
    assert "Production" in url


def test_custom_api_validation_path_traversal_dot_dot():
    """Reject version=".." to prevent path traversal."""
    with pytest.raises(ValueError, match="must not contain '.' or '..' path-traversal"):
        build_url(
            environment="Production",
            company_id="abc-123",
            entity_set_name="engineOverviews",
            publisher="contoso",
            group="technical",
            version="..",
        )


def test_custom_api_validation_slash_in_segment():
    """Reject publisher="a/b" to prevent path traversal."""
    with pytest.raises(ValueError, match="must not contain '/' or '\\\\'"):
        build_url(
            environment="Production",
            company_id="abc-123",
            entity_set_name="engineOverviews",
            publisher="a/b",
            group="technical",
            version="v1.5",
        )


def test_metadata_url_validation_path_traversal():
    """Reject path traversal in metadata URL builder."""
    with pytest.raises(ValueError, match="must not contain '.' or '..' path-traversal"):
        build_metadata_url(
            environment="Production",
            publisher="contoso",
            group="..",
            version="v1.5",
        )
