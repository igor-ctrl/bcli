"""Tests for SafeContext write safety gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bcli.client._async import AsyncBCClient
from bcli.client._safety import DomainRule, SafeContext
from bcli.errors import SafetyError


def _mock_client() -> MagicMock:
    """Create a mock AsyncBCClient with the surface SafeContext touches.

    Post v0004, SafeContext talks to the underlying transport directly via
    ``client._ensure_transport()`` and resolves URLs via
    ``client._resolve_url_for_target(env, company_id, ...)``. The mock has
    to expose both, plus return a transport with async post/patch/delete.
    """
    client = MagicMock()
    transport = MagicMock()
    transport.post = AsyncMock(return_value={"id": "new-1", "status": "Draft"})
    transport.patch = AsyncMock(return_value={"id": "1", "name": "Updated"})
    transport.delete = AsyncMock(return_value={})
    client._ensure_transport.return_value = transport
    # Default URL resolver returns a sentinel string per call so tests can
    # distinguish post/patch/delete URLs.
    client._resolve_url_for_target = MagicMock(
        side_effect=lambda env, cid, entity, **kw: (
            f"https://api.test/{env}/{cid}/{entity}"
            + (f"({kw['record_id']})" if kw.get("record_id") else "")
        )
    )
    # Keep a handle to transport for assertions.
    client._mock_transport = transport
    return client


# ── Construction safety checks ────────────────────────────────────────────

class TestSafeContextConstruction:
    def test_requires_environment(self):
        with pytest.raises(SafetyError, match="environment"):
            SafeContext(client=_mock_client(), environment="", company_id="c-1")

    def test_requires_company_id(self):
        with pytest.raises(SafetyError, match="company_id"):
            SafeContext(client=_mock_client(), environment="Sandbox", company_id="")

    def test_production_requires_confirmation(self):
        with pytest.raises(SafetyError, match="confirm_production"):
            SafeContext(
                client=_mock_client(),
                environment="Production",
                company_id="c-1",
            )

    def test_production_with_confirmation_succeeds(self):
        sc = SafeContext(
            client=_mock_client(),
            environment="Production",
            company_id="c-1",
            confirm_production=True,
        )
        assert sc.environment == "Production"

    def test_sandbox_no_confirmation_needed(self):
        sc = SafeContext(
            client=_mock_client(),
            environment="Sandbox",
            company_id="c-1",
        )
        assert sc.environment == "Sandbox"
        assert sc.company_id == "c-1"

    def test_prod_shorthand_requires_confirmation(self):
        with pytest.raises(SafetyError, match="confirm_production"):
            SafeContext(
                client=_mock_client(),
                environment="Prod",
                company_id="c-1",
            )


# ── Context manager ──────────────────────────────────────────────────────

class TestSafeContextManager:
    async def test_async_context_manager(self):
        async with SafeContext(
            client=_mock_client(), environment="Sandbox", company_id="c-1",
        ) as sw:
            assert sw.environment == "Sandbox"


# ── Domain rules ──────────────────────────────────────────────────────────

class TestDomainRules:
    async def test_finance_domain_adds_draft_status(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post("salesInvoices", body={"customerNumber": "10000"}, domain="finance")

        # The body passed to transport.post should have status=Draft.
        body = client._mock_transport.post.call_args.kwargs["json_body"]
        assert body["status"] == "Draft"

    async def test_finance_domain_preserves_explicit_status(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post(
                "salesInvoices",
                body={"customerNumber": "10000", "status": "Open"},
                domain="finance",
            )

        body = client._mock_transport.post.call_args.kwargs["json_body"]
        assert body["status"] == "Open"

    async def test_standard_domain_no_draft(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post("items", body={"name": "Widget"}, domain="standard")

        body = client._mock_transport.post.call_args.kwargs["json_body"]
        assert "status" not in body

    async def test_custom_domain_rules(self):
        client = _mock_client()
        custom_rules = {
            "hr": DomainRule(allow_write=False),
        }
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
            domain_rules=custom_rules,
        ) as sw:
            with pytest.raises(SafetyError, match="not allowed"):
                await sw.post("employees", body={"name": "Test"}, domain="hr")

        # Disallowed domain must not reach the transport.
        client._mock_transport.post.assert_not_called()

    async def test_unknown_domain_uses_default_rule(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post("customEntities", body={"key": "val"}, domain="custom")

        # Unknown domain gets DomainRule() defaults — allow_write=True, no draft.
        client._mock_transport.post.assert_called_once()


# ── Target binding (vuln-0004 regression) ─────────────────────────────────
#
# Before the fix SafeContext.post/patch/delete delegated to the underlying
# client's profile-bound write methods. The explicit (env, company_id)
# passed to safe_write(...) was stored as metadata only, so writes still
# went to whatever the client profile pointed at — defeating the safety
# guarantee documented in the README and changelog.
#
# These tests assert that the URL resolver is invoked with the SafeContext
# target and that the transport sees the resulting URL.

class TestSafeContextTargetBinding:
    async def test_post_uses_explicit_target(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.post("items", body={"name": "Widget"})

        client._resolve_url_for_target.assert_called_once_with(
            "Sandbox", "c-SANDBOX", "items",
            publisher=None, group=None, version=None,
        )
        url = client._mock_transport.post.call_args.args[0]
        assert "Sandbox" in url and "c-SANDBOX" in url
        assert client._mock_transport.post.call_args.kwargs == {
            "json_body": {"name": "Widget"},
        }

    async def test_patch_uses_explicit_target(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.patch("items", "item-1", body={"name": "Updated"})

        client._resolve_url_for_target.assert_called_once_with(
            "Sandbox", "c-SANDBOX", "items",
            record_id="item-1",
            publisher=None, group=None, version=None,
        )
        url = client._mock_transport.patch.call_args.args[0]
        assert "Sandbox" in url and "c-SANDBOX" in url
        assert client._mock_transport.patch.call_args.kwargs == {
            "json_body": {"name": "Updated"},
            "etag": "*",
        }

    async def test_delete_uses_explicit_target(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.delete("items", "item-1")

        client._resolve_url_for_target.assert_called_once_with(
            "Sandbox", "c-SANDBOX", "items",
            record_id="item-1",
            publisher=None, group=None, version=None,
        )
        url = client._mock_transport.delete.call_args.args[0]
        assert "Sandbox" in url and "c-SANDBOX" in url
        assert client._mock_transport.delete.call_args.kwargs == {"etag": "*"}

    async def test_route_override_is_propagated(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.post(
                "customEntities", body={"key": "val"},
                publisher="contoso", group="sales", version="v2.0",
            )

        client._resolve_url_for_target.assert_called_once_with(
            "Sandbox", "c-SANDBOX", "customEntities",
            publisher="contoso", group="sales", version="v2.0",
        )

    async def test_does_not_call_profile_bound_write_methods(self):
        """Defense-in-depth: the profile-bound client.post/patch/delete must
        never be invoked from SafeContext, otherwise wrong-environment writes
        could regress silently.
        """
        client = _mock_client()
        # Make profile-bound methods loud.
        client.post = AsyncMock(side_effect=AssertionError("client.post called"))
        client.patch = AsyncMock(side_effect=AssertionError("client.patch called"))
        client.delete = AsyncMock(side_effect=AssertionError("client.delete called"))

        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.post("items", body={"name": "Widget"})
            await sw.patch("items", "item-1", body={"name": "X"})
            await sw.delete("items", "item-1")


# ── End-to-end target binding through the real AsyncBCClient ──────────────
#
# The strix PoC built a real ``AsyncBCClient`` bound to Production +
# company-PROD, opened ``client.safe_write("Sandbox", "company-SANDBOX")``,
# and observed that the URLs handed to the transport still contained the
# Production target. This class exercises the same path with a stub
# transport so the URL used at the wire level can be inspected.

class TestEndToEndTargetBinding:
    @pytest.fixture
    def stub_transport(self) -> MagicMock:
        t = MagicMock()
        t.post = AsyncMock(return_value={"id": "created"})
        t.patch = AsyncMock(return_value={"id": "updated"})
        t.delete = AsyncMock(return_value={})
        return t

    def _make_client(self, transport: MagicMock) -> AsyncBCClient:
        client = AsyncBCClient(
            tenant_id="tenant-1",
            client_id="client-1",
            client_secret="secret-1",
            environment="Production",
            company_id="company-PROD",
        )
        # Don't make a real auth/HTTP call.
        client._ensure_transport = lambda: transport  # type: ignore[method-assign]
        return client

    async def test_post_url_targets_safe_context_not_profile(self, stub_transport):
        client = self._make_client(stub_transport)
        async with client.safe_write(
            "Sandbox", "company-SANDBOX", confirm_production=False,
        ) as sw:
            await sw.post("items", {"displayName": "poc"})

        url = stub_transport.post.call_args.args[0]
        assert "/Sandbox/" in url, f"expected Sandbox in URL, got: {url}"
        assert "company-SANDBOX" in url, f"expected company-SANDBOX in URL, got: {url}"
        assert "/Production/" not in url, f"profile env leaked into URL: {url}"
        assert "company-PROD" not in url, f"profile company leaked into URL: {url}"

    async def test_patch_url_targets_safe_context_not_profile(self, stub_transport):
        client = self._make_client(stub_transport)
        async with client.safe_write("Sandbox", "company-SANDBOX") as sw:
            await sw.patch("items", "record-1", {"displayName": "patched"})

        url = stub_transport.patch.call_args.args[0]
        assert "/Sandbox/" in url
        assert "company-SANDBOX" in url
        assert url.endswith("items(record-1)")
        assert "/Production/" not in url
        assert "company-PROD" not in url

    async def test_delete_url_targets_safe_context_not_profile(self, stub_transport):
        client = self._make_client(stub_transport)
        async with client.safe_write("Sandbox", "company-SANDBOX") as sw:
            await sw.delete("items", "record-1")

        url = stub_transport.delete.call_args.args[0]
        assert "/Sandbox/" in url
        assert "company-SANDBOX" in url
        assert url.endswith("items(record-1)")
        assert "/Production/" not in url
        assert "company-PROD" not in url
