"""Tests for SafeContext write safety gate."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bcli.client._safety import DEFAULT_DOMAIN_RULES, DomainRule, SafeContext
from bcli.errors import SafetyError


def _mock_client() -> AsyncMock:
    """Create a mock AsyncBCClient with async write methods."""
    client = AsyncMock()
    client.post.return_value = {"id": "new-1", "status": "Draft"}
    client.patch.return_value = {"id": "1", "name": "Updated"}
    client.delete.return_value = {}
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

        # The body passed to client.post should have status=Draft
        call_args = client.post.call_args
        body = call_args.args[1]
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

        body = client.post.call_args.args[1]
        assert body["status"] == "Open"

    async def test_standard_domain_no_draft(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post("items", body={"name": "Widget"}, domain="standard")

        body = client.post.call_args.args[1]
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

    async def test_unknown_domain_uses_default_rule(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post("customEntities", body={"key": "val"}, domain="custom")

        # Unknown domain gets DomainRule() defaults — allow_write=True, no draft
        client.post.assert_called_once()


# ── Write methods delegate correctly ──────────────────────────────────────

class TestSafeContextDelegation:
    async def test_post_delegates_to_client(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            result = await sw.post("items", body={"name": "Widget"})

        client.post.assert_called_once_with(
            "items", {"name": "Widget"},
            publisher=None, group=None, version=None,
        )

    async def test_patch_delegates_to_client(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.patch("items", "item-1", body={"name": "Updated"})

        client.patch.assert_called_once_with(
            "items", "item-1", {"name": "Updated"},
            etag="*", publisher=None, group=None, version=None,
        )

    async def test_delete_delegates_to_client(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.delete("items", "item-1")

        client.delete.assert_called_once_with(
            "items", "item-1",
            etag="*", publisher=None, group=None, version=None,
        )

    async def test_post_with_route_override(self):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post(
                "customEntities", body={"key": "val"},
                publisher="contoso", group="sales", version="v2.0",
            )

        client.post.assert_called_once_with(
            "customEntities", {"key": "val"},
            publisher="contoso", group="sales", version="v2.0",
        )
