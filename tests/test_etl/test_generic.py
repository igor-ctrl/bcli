"""Tests for the generic ETL layer — no profile/registry coupling."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import httpx
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


_BASE = (
    "https://api.businesscentral.dynamics.com/v2.0/Sandbox/api/v2.0/"
    "companies(00000000-0000-0000-0000-000000000000)/customers"
)


def _serve(client: BCClient, *responses: httpx.Response) -> list[httpx.Request]:
    """Answer the client's requests from ``responses`` in order; return what was sent."""
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    client._t()._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return seen


@pytest.mark.asyncio
async def test_bcclient_get_injects_bearer():
    auth = StaticTokenAuth(lambda: _async_val("tok-1"))

    async with BCClient(auth=auth, environment="Sandbox") as client:
        seen = _serve(client, httpx.Response(200, json={"value": []}))
        await client.get(_BASE)
    assert seen[0].headers["Authorization"] == "Bearer tok-1"


@pytest.mark.asyncio
async def test_bcclient_accepts_bcli_auth_provider():
    """A bcli provider (``get_access_token``) is used as-is, no adapter."""
    auth = AsyncMock()
    auth.get_access_token = AsyncMock(return_value="tok-bcli")

    async with BCClient(auth=auth, environment="Sandbox") as client:
        seen = _serve(client, httpx.Response(200, json={"value": []}))
        await client.get(_BASE)
    assert seen[0].headers["Authorization"] == "Bearer tok-bcli"


@pytest.mark.asyncio
async def test_bcclient_404_raises_not_found():
    auth = StaticTokenAuth(lambda: _async_val("tok"))
    async with BCClient(auth=auth, environment="Sandbox") as client:
        _serve(client, httpx.Response(404, json={"error": {"code": "NotFound", "message": "x"}}))
        with pytest.raises(NotFoundError):
            await client.get(_BASE)


@pytest.mark.asyncio
async def test_bcclient_paginates_nextlink():
    auth = StaticTokenAuth(lambda: _async_val("tok"))
    async with BCClient(auth=auth, environment="Sandbox") as client:
        seen = _serve(
            client,
            httpx.Response(200, json={
                "value": [{"id": "1"}, {"id": "2"}],
                "@odata.nextLink": f"{_BASE}?$skiptoken=p2",
            }),
            httpx.Response(200, json={"value": [{"id": "3"}]}),
        )
        results = [page async for page in client.paginate(_BASE, params={"$top": "2"})]
    assert results == [[{"id": "1"}, {"id": "2"}], [{"id": "3"}]]
    assert seen[0].url.params["$top"] == "2"
    assert seen[1].url.params["$skiptoken"] == "p2"


@pytest.mark.asyncio
async def test_bcclient_refuses_off_origin_nextlink():
    """An off-origin ``@odata.nextLink`` never receives the bearer token."""
    auth = StaticTokenAuth(lambda: _async_val("tok"))
    async with BCClient(auth=auth, environment="Sandbox") as client:
        seen = _serve(client, httpx.Response(200, json={
            "value": [{"id": "1"}],
            "@odata.nextLink": "https://attacker.example/leak",
        }))
        with pytest.raises(ValueError, match="off-origin"):
            [page async for page in client.paginate(_BASE)]
    assert [str(r.url.host) for r in seen] == ["api.businesscentral.dynamics.com"]


def test_build_entity_url_rejects_bad_segment():
    with pytest.raises(ValueError):
        build_entity_url(
            environment="Sandbox",
            company_id="00000000-0000-0000-0000-000000000000",
            entity_set_name="customers/../../evil",
        )


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
    """The generic layer never sees a profile, registry, config or bcli auth.

    ``_client.py`` runs on bcli's transport, so it alone may import the
    transport-level modules (``bcli._url``, ``bcli.client._transport``,
    ``bcli.errors``, ``bcli.odata``). Nothing in the generic layer may import
    ``bcli.registry``, ``bcli.config`` or ``bcli.auth``, nor the client
    package beyond its transport.
    """

    def test_generic_does_not_import_bcli_sdk(self):
        # Fresh import inspection — check source files themselves
        import ast
        import pathlib

        etl_dir = pathlib.Path(
            sys.modules["bcli.etl"].__file__  # type: ignore[arg-type]
        ).parent
        transport_only = ("bcli._url", "bcli.client._transport", "bcli.errors", "bcli.odata")

        for module_name in ("_generic.py", "_client.py", "_auth.py", "_stampers.py", "_stamper_factory.py"):
            allowed = ("bcli.etl",) + (transport_only if module_name == "_client.py" else ())
            tree = ast.parse((etl_dir / module_name).read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                for name in names:
                    if name == "bcli" or name.startswith("bcli."):
                        assert name.startswith(allowed), f"{module_name} imports forbidden {name}"


# ─── Helpers ─────────────────────────────────────────────────────────


async def _async_val(v):
    return v
