"""Tests for CLIState.make_async_client — ensures -c/--company reaches the HTTP layer.

Regression: previously AsyncBCClient(profile=state.profile_name, config=state.config)
was called directly in every command, which re-read the raw profile from config and
ignored state.company_override. The banner showed the alias (via state.profile) but
the client talked to the profile's default company_id. The two disagreed — the
banner lied.
"""

from __future__ import annotations

import pytest

from bcli.config._model import BCConfig, BCDefaults, BCProfile, CompanyAlias
from bcli_cli._state import CLIState


@pytest.fixture
def prod_profile_with_aliases() -> BCProfile:
    return BCProfile(
        tenant_id="t1",
        environment="Production",
        company_id="default-guid",
        company_name="Default Co",
        companies={
            "LLC": CompanyAlias(id="llc-guid", name="LLC Co"),
            "Corp": CompanyAlias(id="corp-guid", name="Corp Co"),
        },
    )


@pytest.fixture
def state_with_config(prod_profile_with_aliases, monkeypatch):
    """Build a clean CLIState whose config has a single 'prod' profile with aliases."""
    config = BCConfig(
        defaults=BCDefaults(profile="prod"),
        profiles={"prod": prod_profile_with_aliases},
    )
    # Avoid any ambient env vars leaking into load_config fallbacks.
    for env_var in ("BCLI_PROFILE", "BCLI_FORMAT", "BCLI_TIMEOUT", "BCLI_SECRET"):
        monkeypatch.delenv(env_var, raising=False)

    s = CLIState()
    s.config = config
    return s


def test_make_async_client_applies_company_override(state_with_config):
    """-c <alias> must reach the client — not just the banner."""
    state_with_config.company_override = "LLC"

    client = state_with_config.make_async_client()
    try:
        assert client.profile.company_id == "llc-guid"
        assert client.profile.company_name == "LLC Co"

        # URL resolution uses the overridden company_id.
        url = client._resolve_url("customers")
        assert "companies(llc-guid)" in url
    finally:
        # No transport was built (lazy), but calling close is still safe.
        import asyncio
        asyncio.run(client.close())


def test_make_async_client_no_override_uses_default(state_with_config):
    """Without -c, the client uses the profile's stored default company."""
    client = state_with_config.make_async_client()
    try:
        assert client.profile.company_id == "default-guid"
        url = client._resolve_url("customers")
        assert "companies(default-guid)" in url
    finally:
        import asyncio
        asyncio.run(client.close())


def test_make_async_client_env_override(state_with_config):
    """-e Sandbox must reach the URL, not just the banner."""
    state_with_config.env_override = "Sandbox"

    client = state_with_config.make_async_client()
    try:
        assert client.profile.environment == "Sandbox"
        url = client._resolve_url("customers")
        assert "/Sandbox/" in url
    finally:
        import asyncio
        asyncio.run(client.close())


def test_make_async_client_all_is_not_treated_as_alias(state_with_config):
    """-c all is a special 'iterate' sentinel, not a company lookup."""
    state_with_config.company_override = "all"

    client = state_with_config.make_async_client()
    try:
        # Falls back to profile default — the iteration loop builds per-company URLs itself.
        assert client.profile.company_id == "default-guid"
    finally:
        import asyncio
        asyncio.run(client.close())


def test_make_async_client_case_insensitive_alias(state_with_config):
    """-c llc should match an alias stored as 'LLC' (case-insensitive)."""
    state_with_config.company_override = "llc"

    client = state_with_config.make_async_client()
    try:
        assert client.profile.company_id == "llc-guid"
    finally:
        import asyncio
        asyncio.run(client.close())


def test_make_async_client_does_not_mutate_stored_config(state_with_config):
    """Override must not leak into state.config — a subsequent save_config would
    otherwise persist the override as the profile's new default."""
    state_with_config.company_override = "LLC"

    client = state_with_config.make_async_client()
    try:
        # Re-check the original config — still the default.
        assert state_with_config.config.profiles["prod"].company_id == "default-guid"
    finally:
        import asyncio
        asyncio.run(client.close())
