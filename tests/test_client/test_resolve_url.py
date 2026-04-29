"""Tests for AsyncBCClient._resolve_url — the URL routing layer.

The critical behaviour we lock in: when a profile sets
``disable_standard_api = true``, requests for entities that are NOT in the
custom registry must fail client-side instead of silently routing to
``/api/v2.0/``. The explicit publisher/group/version override remains an
intentional escape hatch.
"""

from __future__ import annotations

import pytest

from bcli.client._async import AsyncBCClient
from bcli.config._model import BCConfig, BCProfile
from bcli.errors import RegistryError


def _make_client(*, disable_standard: bool, profile_name: str = "test") -> AsyncBCClient:
    """Build an AsyncBCClient with a fresh in-memory profile (no token cache)."""
    profile = BCProfile(
        tenant_id="t1",
        environment="Sandbox",
        company_id="company-guid-000",
        client_id="cid",
        disable_standard_api=disable_standard,
    )
    config = BCConfig(profiles={profile_name: profile})
    config.defaults.profile = profile_name
    return AsyncBCClient(profile=profile_name, config=config)


class TestDisableStandardApiLockdown:
    def test_unknown_entity_blocked_when_standard_disabled(self):
        client = _make_client(disable_standard=True)
        with pytest.raises(RegistryError, match="disable_standard_api"):
            client._resolve_url("salesInvoices")

    def test_error_message_actionable(self):
        """The error should point the user at concrete next commands."""
        client = _make_client(disable_standard=True)
        with pytest.raises(RegistryError) as excinfo:
            client._resolve_url("vendors")
        msg = str(excinfo.value)
        assert "bcli endpoint list" in msg
        assert "bcli registry import" in msg
        assert "--publisher" in msg

    def test_explicit_override_still_allowed(self):
        """Explicit publisher/group/version is the documented escape hatch."""
        client = _make_client(disable_standard=True)
        url = client._resolve_url(
            "salesInvoices",
            publisher="acme", group="finance", version="v1.5",
        )
        assert "/api/acme/finance/v1.5/" in url
        assert url.endswith("salesInvoices")

    def test_unknown_entity_routes_to_v20_when_standard_enabled(self):
        """Default profile (standard catalog enabled): legacy fallback still works."""
        client = _make_client(disable_standard=False)
        url = client._resolve_url("vendors")
        assert "/api/v2.0/" in url
        assert url.endswith("vendors")

    def test_known_standard_entity_routes_correctly(self):
        """Standard v2.0 catalog → /api/v2.0/ when not disabled."""
        client = _make_client(disable_standard=False)
        url = client._resolve_url("customers")
        assert "/api/v2.0/" in url
