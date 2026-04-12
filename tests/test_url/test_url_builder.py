"""Tests for URL builder."""

from bcapi._url import build_companies_url, build_url


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
        publisher="acme",
        group="technical",
        version="v1.5",
    )
    assert "api/acme/technical/v1.5" in url
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
