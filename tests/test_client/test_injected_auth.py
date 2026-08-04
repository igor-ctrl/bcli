"""Tests for AsyncBCClient(auth=...) — handing the client a ready auth provider.

Before this, `_ensure_transport` always built an auth provider from the profile's
`auth_method`, so a profile marked `browser` could only ever authenticate by
opening a browser and binding a loopback listener on the host running the process.
That makes the SDK unusable from a server, where the human is somewhere else
entirely.

Injecting a provider is the seam. The profile still supplies everything else —
environment, company, registry, and the `disable_standard_api` gate — so the
security-relevant routing behaviour is unchanged; only credential acquisition
moves out.
"""

from __future__ import annotations

import pytest

from bcli.auth._static import StaticTokenAuth
from bcli.client._async import AsyncBCClient
from bcli.config._model import BCConfig, BCProfile


def _client(
    *,
    auth=None,
    auth_method: str = "browser",
    profile_name: str = "test",
) -> AsyncBCClient:
    profile = BCProfile(
        tenant_id="t1",
        environment="Sandbox",
        company_id="company-guid-000",
        client_id="cid",
        auth_method=auth_method,
    )
    config = BCConfig(profiles={profile_name: profile})
    config.defaults.profile = profile_name
    return AsyncBCClient(profile=profile_name, config=config, auth=auth)


class TestInjectedAuthIsUsed:
    def test_transport_receives_the_injected_provider(self):
        auth = StaticTokenAuth("tok")
        transport = _client(auth=auth)._ensure_transport()
        assert transport._auth is auth

    def test_injected_auth_overrides_a_browser_profile(self):
        """The decisive case: a `browser` profile must not try to open a browser
        when the caller already supplied a token."""
        auth = StaticTokenAuth("tok")
        transport = _client(auth=auth, auth_method="browser")._ensure_transport()
        assert transport._auth is auth
        assert type(transport._auth).__name__ != "BrowserAuth"

    def test_injected_auth_overrides_client_credentials_without_needing_a_secret(self):
        """No BCLI_CLIENT_SECRET, no keyring entry — and no error, because the
        credential path is bypassed entirely."""
        auth = StaticTokenAuth("tok")
        transport = _client(auth=auth, auth_method="client_credentials")._ensure_transport()
        assert transport._auth is auth

    def test_injected_auth_bypasses_auth_method_validation(self):
        """An unsupported `auth_method` normally raises ConfigError. With injected
        auth it is never consulted, so a profile that only ever runs server-side
        need not name a flow it cannot perform."""
        transport = _client(auth=StaticTokenAuth("tok"), auth_method="nonsense")._ensure_transport()
        assert isinstance(transport._auth, StaticTokenAuth)

    def test_transport_is_built_once_and_reused(self):
        client = _client(auth=StaticTokenAuth("tok"))
        assert client._ensure_transport() is client._ensure_transport()


class TestExistingBehaviourPreserved:
    def test_browser_profile_still_builds_browser_auth_when_nothing_injected(self):
        transport = _client(auth_method="browser")._ensure_transport()
        assert type(transport._auth).__name__ == "BrowserAuth"

    def test_device_code_profile_still_builds_device_code_auth(self):
        transport = _client(auth_method="device_code")._ensure_transport()
        assert type(transport._auth).__name__ == "DeviceCodeAuth"

    def test_unsupported_auth_method_still_raises_without_injection(self):
        from bcli.errors import ConfigError

        with pytest.raises(ConfigError, match="Unsupported auth_method"):
            _client(auth_method="nonsense")._ensure_transport()


class TestProgrammaticMode:
    def test_injected_auth_works_without_any_config_file(self):
        auth = StaticTokenAuth("tok")
        client = AsyncBCClient(
            tenant_id="t1",
            environment="Production",
            company_id="company-guid-000",
            auth=auth,
        )
        assert client._ensure_transport()._auth is auth


class TestRegistryGateStillApplies:
    def test_disable_standard_api_still_blocks_unknown_entities(self):
        """Injecting auth must not weaken the routing gate — that lives on the
        profile and registry, not on the credential path."""
        from bcli.errors import RegistryError

        profile = BCProfile(
            tenant_id="t1",
            environment="Sandbox",
            company_id="company-guid-000",
            client_id="cid",
            disable_standard_api=True,
        )
        config = BCConfig(profiles={"p": profile})
        config.defaults.profile = "p"
        client = AsyncBCClient(profile="p", config=config, auth=StaticTokenAuth("tok"))

        with pytest.raises(RegistryError):
            client._resolve_url_for_target(
                "Sandbox", "company-guid-000", "definitelyNotInAnyRegistry"
            )
