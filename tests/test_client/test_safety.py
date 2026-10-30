"""Tests for SafeContext write safety gate."""

from __future__ import annotations

import pytest

from bcli.client._async import AsyncBCClient
from bcli.client._safety import DomainRule, SafeContext
from bcli.errors import SafetyError


def _mock_client() -> AsyncBCClient:
    """A real programmatic client bound to Production + company-PROD.

    Tests that send pair it with ``fake_bc`` so the wire URL can be inspected;
    the profile target is deliberately *not* what SafeContext binds to.
    """
    return AsyncBCClient(
        tenant_id="tenant-1",
        client_id="client-1",
        client_secret="secret-1",
        environment="Production",
        company_id="company-PROD",
    )


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
    async def test_finance_domain_adds_draft_status(self, fake_bc):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post("salesInvoices", body={"customerNumber": "10000"}, domain="finance")

        # The body sent to BC should have status=Draft.
        body = fake_bc.last.json_body
        assert body["status"] == "Draft"

    async def test_finance_domain_preserves_explicit_status(self, fake_bc):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post(
                "salesInvoices",
                body={"customerNumber": "10000", "status": "Open"},
                domain="finance",
            )

        body = fake_bc.last.json_body
        assert body["status"] == "Open"

    async def test_standard_domain_no_draft(self, fake_bc):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post("items", body={"name": "Widget"}, domain="standard")

        body = fake_bc.last.json_body
        assert "status" not in body

    async def test_custom_domain_rules(self, fake_bc):
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
        assert fake_bc.sent == []

    async def test_unknown_domain_uses_default_rule(self, fake_bc):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-1",
        ) as sw:
            await sw.post("customEntities", body={"key": "val"}, domain="custom")

        # Unknown domain gets DomainRule() defaults — allow_write=True, no draft.
        assert [r.method for r in fake_bc.sent] == ["POST"]


# ── Target binding (vuln-0004 regression) ─────────────────────────────────
#
# Before the fix SafeContext.post/patch/delete delegated to the underlying
# client's profile-bound write methods. The explicit (env, company_id)
# passed to safe_write(...) was stored as metadata only, so writes still
# went to whatever the client profile pointed at — defeating the safety
# guarantee documented in the README and changelog.
#
# These tests drive the real client and assert on the URL BC receives.

class TestSafeContextTargetBinding:
    async def test_post_uses_explicit_target(self, fake_bc):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.post("items", body={"name": "Widget"})

        sent = fake_bc.last
        assert sent.method == "POST"
        assert sent.url == client.resolve(
            "items", environment="Sandbox", company_id="c-SANDBOX",
        )
        assert sent.json_body == {"name": "Widget"}
        assert sent.etag is None

    async def test_patch_uses_explicit_target(self, fake_bc):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.patch("items", "item-1", body={"name": "Updated"})

        sent = fake_bc.last
        assert sent.method == "PATCH"
        assert sent.url == client.resolve(
            "items", record_id="item-1", environment="Sandbox", company_id="c-SANDBOX",
        )
        assert sent.json_body == {"name": "Updated"}
        assert sent.etag == "*"

    async def test_delete_uses_explicit_target(self, fake_bc):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.delete("items", "item-1")

        sent = fake_bc.last
        assert sent.method == "DELETE"
        assert sent.url == client.resolve(
            "items", record_id="item-1", environment="Sandbox", company_id="c-SANDBOX",
        )
        assert sent.json_body is None
        assert sent.etag == "*"

    async def test_route_override_is_propagated(self, fake_bc):
        client = _mock_client()
        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.post(
                "customEntities", body={"key": "val"},
                publisher="contoso", group="sales", version="v2.0",
            )

        url = fake_bc.last.url
        assert "/api/contoso/sales/v2.0/" in url
        assert "/Sandbox/" in url and "c-SANDBOX" in url

    async def test_does_not_call_profile_bound_write_methods(self, fake_bc, monkeypatch):
        """Defense-in-depth: the profile-bound client.post/patch/delete must
        never be invoked from SafeContext, otherwise wrong-environment writes
        could regress silently.
        """
        client = _mock_client()

        async def boom(*a, **kw):
            raise AssertionError("profile-bound write method called")

        for name in ("post", "patch", "delete"):
            monkeypatch.setattr(client, name, boom)

        async with SafeContext(
            client=client, environment="Sandbox", company_id="c-SANDBOX",
        ) as sw:
            await sw.post("items", body={"name": "Widget"})
            await sw.patch("items", "item-1", body={"name": "X"})
            await sw.delete("items", "item-1")

        assert [r.method for r in fake_bc.sent] == ["POST", "PATCH", "DELETE"]


# ── End-to-end target binding through the real AsyncBCClient ──────────────
#
# The strix PoC built a real ``AsyncBCClient`` bound to Production +
# company-PROD, opened ``client.safe_write("Sandbox", "company-SANDBOX")``,
# and observed that the URLs handed to the transport still contained the
# Production target. This class exercises the same path through ``fake_bc``
# so the URL used at the wire level can be inspected.

class TestEndToEndTargetBinding:
    async def test_post_url_targets_safe_context_not_profile(self, fake_bc):
        client = _mock_client()
        async with client.safe_write(
            "Sandbox", "company-SANDBOX", confirm_production=False,
        ) as sw:
            await sw.post("items", {"displayName": "poc"})

        url = fake_bc.last.url
        assert "/Sandbox/" in url, f"expected Sandbox in URL, got: {url}"
        assert "company-SANDBOX" in url, f"expected company-SANDBOX in URL, got: {url}"
        assert "/Production/" not in url, f"profile env leaked into URL: {url}"
        assert "company-PROD" not in url, f"profile company leaked into URL: {url}"

    async def test_patch_url_targets_safe_context_not_profile(self, fake_bc):
        client = _mock_client()
        async with client.safe_write("Sandbox", "company-SANDBOX") as sw:
            await sw.patch("items", "record-1", {"displayName": "patched"})

        url = fake_bc.last.url
        assert "/Sandbox/" in url
        assert "company-SANDBOX" in url
        assert url.endswith("items(record-1)")
        assert "/Production/" not in url
        assert "company-PROD" not in url

    async def test_delete_url_targets_safe_context_not_profile(self, fake_bc):
        client = _mock_client()
        async with client.safe_write("Sandbox", "company-SANDBOX") as sw:
            await sw.delete("items", "record-1")

        url = fake_bc.last.url
        assert "/Sandbox/" in url
        assert "company-SANDBOX" in url
        assert url.endswith("items(record-1)")
        assert "/Production/" not in url
        assert "company-PROD" not in url
