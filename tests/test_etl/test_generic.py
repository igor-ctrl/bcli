"""Tests for the generic ETL layer — no bcli coupling."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("dlt")

from bcli.etl._auth import StaticTokenAuth
from bcli.etl._client import BCClient, NotFoundError, build_entity_url
from bcli.etl._generic import EntityDef, business_central


# ─── EntityDef ───────────────────────────────────────────────────────


class TestEntityDef:
    def test_frozen(self):
        e = EntityDef("customers")
        with pytest.raises(AttributeError):
            e.name = "other"

    def test_defaults(self):
        e = EntityDef("customers")
        assert e.primary_key == "systemId"
        assert e.cursor_field == "systemModifiedAt"
        assert e.write_disposition == "merge"
        assert e.api_publisher is None

    def test_custom_primary_key(self):
        e = EntityDef("items", primary_key="id")
        assert e.primary_key == "id"

    def test_no_cursor(self):
        e = EntityDef("lookups", cursor_field=None)
        assert e.cursor_field is None


# ─── URL builder ─────────────────────────────────────────────────────


class TestBuildEntityUrl:
    def test_standard_v2_url(self):
        url = build_entity_url(
            environment="Production",
            company_id="co-1",
            entity_set_name="customers",
        )
        assert "/api/v2.0/companies(co-1)/customers" in url

    def test_custom_api_url(self):
        url = build_entity_url(
            environment="Production",
            company_id="co-1",
            entity_set_name="glEntries",
            publisher="acme",
            group="finance",
            version="v1.0",
        )
        assert "/api/acme/finance/v1.0/companies(co-1)/glEntries" in url


# ─── AuthProviders ───────────────────────────────────────────────────


class TestStaticTokenAuth:
    @pytest.mark.asyncio
    async def test_delegates_to_callback(self):
        async def _token():
            return "token-xyz"

        auth = StaticTokenAuth(_token)
        assert await auth.get_token() == "token-xyz"


# ─── BCClient ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bcclient_get_injects_bearer():
    auth = StaticTokenAuth(lambda: _async_val("tok-1"))

    async with BCClient(auth=auth, environment="Sandbox") as client:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = b'{"value": []}'
        mock_response.json = lambda: {"value": []}
        with patch.object(client._http, "request", return_value=mock_response) as mock_req:
            await client.get("https://api.businesscentral.dynamics.com/v2.0/Sandbox/api/v2.0/companies(00000000-0000-0000-0000-000000000000)/customers")
        call = mock_req.await_args
        assert call.kwargs["headers"]["Authorization"] == "Bearer tok-1"


@pytest.mark.asyncio
async def test_bcclient_404_raises_not_found():
    auth = StaticTokenAuth(lambda: _async_val("tok"))
    async with BCClient(auth=auth, environment="Sandbox") as client:
        mock_response = AsyncMock()
        mock_response.status_code = 404
        mock_response.content = b""
        with patch.object(client._http, "request", return_value=mock_response):
            with pytest.raises(NotFoundError):
                await client.get("https://api.businesscentral.dynamics.com/v2.0/Sandbox/api/v2.0/companies(00000000-0000-0000-0000-000000000000)/customers")


@pytest.mark.asyncio
async def test_bcclient_paginates_nextlink():
    auth = StaticTokenAuth(lambda: _async_val("tok"))
    async with BCClient(auth=auth, environment="Sandbox") as client:
        base = (
            "https://api.businesscentral.dynamics.com/v2.0/Sandbox/api/v2.0/"
            "companies(00000000-0000-0000-0000-000000000000)/customers"
        )
        page1 = _mock_ok_response({
            "value": [{"id": "1"}, {"id": "2"}],
            "@odata.nextLink": f"{base}?$skiptoken=p2",
        })
        page2 = _mock_ok_response({"value": [{"id": "3"}]})
        with patch.object(client._http, "request", side_effect=[page1, page2]):
            results = [page async for page in client.paginate(f"{base}?$skiptoken=p1")]
        assert len(results) == 2
        assert results[0] == [{"id": "1"}, {"id": "2"}]
        assert results[1] == [{"id": "3"}]


# ─── business_central source ─────────────────────────────────────────


class TestBusinessCentralSource:
    def test_requires_auth_or_credentials(self):
        with pytest.raises(ValueError, match="Pass either"):
            business_central(
                environment="Sandbox",
                entities=[EntityDef("customers")],
            )

    def test_builds_with_explicit_auth(self):
        async def _token():
            return "t"

        source = business_central(
            auth=StaticTokenAuth(_token),
            environment="Sandbox",
            entities=[EntityDef("customers"), EntityDef("vendors")],
            multi_company=True,
        )
        names = {r.name for r in source.resources.values()}
        assert names == {"customers", "vendors"}

    def test_builds_with_credentials(self):
        source = business_central(
            tenant_id="t", client_id="c", client_secret="s",
            environment="Sandbox",
            entities=[EntityDef("customers")],
            multi_company=True,
        )
        assert "customers" in source.resources


# ─── Import rule: no bcli coupling ───────────────────────────────────


class TestGenericHasNoBcliCoupling:
    """The generic layer must be importable with only bcli.etl modules loaded.

    If any of _generic.py, _client.py, _auth.py, _stampers.py,
    _stamper_factory.py imports from bcli.registry, bcli.client,
    bcli.config, or bcli.auth, this test fails.
    """

    def test_generic_does_not_import_bcli_sdk(self):
        # Fresh import inspection — check source files themselves
        import ast
        import pathlib

        etl_dir = pathlib.Path(
            sys.modules["bcli.etl"].__file__  # type: ignore[arg-type]
        ).parent
        forbidden_prefixes = ("bcli.registry", "bcli.client", "bcli.config", "bcli.auth", "bcli.errors")

        for module_name in ("_generic.py", "_client.py", "_auth.py", "_stampers.py", "_stamper_factory.py"):
            path = etl_dir / module_name
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith(forbidden_prefixes), (
                        f"{module_name} imports forbidden {node.module}"
                    )
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith(forbidden_prefixes), (
                            f"{module_name} imports forbidden {alias.name}"
                        )


# ─── Helpers ─────────────────────────────────────────────────────────


async def _async_val(v):
    return v


def _mock_ok_response(body: dict):
    from unittest.mock import AsyncMock
    m = AsyncMock()
    m.status_code = 200
    m.content = json.dumps(body).encode()
    m.json = lambda: body
    return m
