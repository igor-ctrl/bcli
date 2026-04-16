"""Tests for the dlt source wrapping bcli SDK."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

dlt = pytest.importorskip("dlt")

from bcli.etl._entities import EntityDef
from bcli.etl._source import _async_extract, _make_resource, _stamp_fivetran_fields, business_central


# ─── Helpers ─────────────────────────────────────────────────────────


def _mock_client(pages: list[list[dict]]) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # query() is sync, returns BoundQuery — use MagicMock for the fluent chain
    query = MagicMock()
    query.orderby.return_value = query
    query.filter.return_value = query
    query.route.return_value = query

    # pages() is async, returns an async iterator
    async def _pages():
        for page in pages:
            yield page

    async def _pages_coro():
        return _pages()

    query.pages = _pages_coro
    client.query = MagicMock(return_value=query)
    return client


def _sample_entities() -> list[EntityDef]:
    return [
        EntityDef("engineOverviews", api_publisher="acme", api_group="standard", api_version="v1.0"),
        EntityDef("leaseHeaders", api_publisher="acme", api_group="standard", api_version="v1.0"),
    ]


# ─── business_central source ─────────────────────────────────────────


class TestBusinessCentralSource:
    def test_loads_from_registry(self):
        """Source loads entities from the sandbox registry."""
        source = business_central(profile="sandbox")
        resource_names = {r.name for r in source.resources.values()}
        # sandbox has 114+ custom endpoints
        assert len(resource_names) > 0

    def test_filters_by_entity_names(self):
        source = business_central(profile="sandbox", entities=["ArchivedAcqHeaders"])
        resource_names = {r.name for r in source.resources.values()}
        assert resource_names == {"ArchivedAcqHeaders"}

    def test_unknown_entity_raises(self):
        with pytest.raises(ValueError, match="Unknown entities"):
            business_central(profile="sandbox", entities=["nonexistent_xyz"])


# ─── _make_resource ──────────────────────────────────────────────────


class TestMakeResource:
    def test_resource_has_correct_name(self):
        entity = EntityDef("engineOverviews", api_publisher="acme", api_group="standard", api_version="v1.0")
        resource = _make_resource(entity, "test")
        assert resource.name == "engineOverviews"

    def test_full_refresh_uses_replace(self):
        entity = EntityDef("engineOverviews")
        resource = _make_resource(entity, "test", full_refresh=True)
        assert resource.write_disposition == "replace"

    def test_incremental_uses_merge(self):
        entity = EntityDef("engineOverviews")
        resource = _make_resource(entity, "test", full_refresh=False)
        assert resource.write_disposition == "merge"


# ─── _async_extract ──────────────────────────────────────────────────


class TestFivetranFields:
    def test_stamp_adds_fields(self):
        page = [{"id": "1", "name": "Acme"}, {"id": "2", "name": "Globex"}]
        result = _stamp_fivetran_fields(page)
        for record in result:
            assert "_fivetran_synced" in record
            assert "_fivetran_deleted" in record
            assert record["_fivetran_deleted"] is False

    def test_stamp_preserves_original_fields(self):
        page = [{"id": "1", "name": "Acme"}]
        result = _stamp_fivetran_fields(page)
        assert result[0]["id"] == "1"
        assert result[0]["name"] == "Acme"

    def test_stamp_does_not_mutate_input(self):
        page = [{"id": "1"}]
        _stamp_fivetran_fields(page)
        assert "_fivetran_synced" not in page[0]

    def test_synced_is_iso_utc(self):
        page = [{"id": "1"}]
        result = _stamp_fivetran_fields(page)
        synced = result[0]["_fivetran_synced"]
        assert "+00:00" in synced or "Z" in synced


class TestAsyncExtract:
    @pytest.mark.asyncio
    async def test_incremental_adds_filter(self):
        entity = EntityDef("engineOverviews", api_publisher="acme", api_group="standard", api_version="v1.0")
        client = _mock_client(pages=[[{"systemId": "1", "name": "CF34"}]])

        with patch("bcli.AsyncBCClient", return_value=client):
            result = await _async_extract(entity, "test", since="2025-01-01T00:00:00Z")

        query = client.query.return_value
        query.filter.assert_called_once()
        filter_arg = query.filter.call_args[0][0]
        assert "systemModifiedAt gt 2025-01-01T00:00:00Z" in filter_arg

    @pytest.mark.asyncio
    async def test_full_refresh_skips_filter(self):
        entity = EntityDef("engineOverviews", api_publisher="acme", api_group="standard", api_version="v1.0")
        client = _mock_client(pages=[[{"systemId": "1"}]])

        with patch("bcli.AsyncBCClient", return_value=client):
            await _async_extract(entity, "test", since=None)

        query = client.query.return_value
        query.filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_all_pages(self):
        entity = EntityDef("engineOverviews")
        page1 = [{"systemId": "1"}, {"systemId": "2"}]
        page2 = [{"systemId": "3"}]
        client = _mock_client(pages=[page1, page2])

        with patch("bcli.AsyncBCClient", return_value=client):
            result = await _async_extract(entity, "test", since=None)

        assert len(result) == 2
        assert len(result[0]) == 2
        assert len(result[1]) == 1

    @pytest.mark.asyncio
    async def test_records_have_fivetran_fields(self):
        entity = EntityDef("engineOverviews")
        client = _mock_client(pages=[[{"systemId": "1", "name": "CF34"}]])

        with patch("bcli.AsyncBCClient", return_value=client):
            result = await _async_extract(entity, "test", since=None)

        record = result[0][0]
        assert record["_fivetran_synced"] is not None
        assert record["_fivetran_deleted"] is False
        assert record["systemId"] == "1"

    @pytest.mark.asyncio
    async def test_orders_by_cursor_field(self):
        entity = EntityDef("engineOverviews")
        client = _mock_client(pages=[])

        with patch("bcli.AsyncBCClient", return_value=client):
            await _async_extract(entity, "test", since=None)

        query = client.query.return_value
        query.orderby.assert_called_once_with("systemModifiedAt asc")

    @pytest.mark.asyncio
    async def test_custom_api_sets_route(self):
        entity = EntityDef("engineOverviews", api_publisher="acme", api_group="standard", api_version="v1.0")
        client = _mock_client(pages=[])

        with patch("bcli.AsyncBCClient", return_value=client):
            await _async_extract(entity, "test", since=None)

        query = client.query.return_value
        query.route.assert_called_once_with("acme", "standard", "v1.0")

    @pytest.mark.asyncio
    async def test_standard_api_skips_route(self):
        entity = EntityDef("customers", api_publisher=None)
        client = _mock_client(pages=[])

        with patch("bcli.AsyncBCClient", return_value=client):
            await _async_extract(entity, "test", since=None)

        query = client.query.return_value
        query.route.assert_not_called()
