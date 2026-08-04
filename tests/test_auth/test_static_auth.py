"""Tests for StaticTokenAuth — supplying an already-acquired access token.

This exists for embedders that obtain a BC token themselves and hand it to the
SDK: a hosted service doing an on-behalf-of exchange per request, a CI job with a
token from its platform, a notebook pasting one in. Those callers cannot use the
browser flow, which needs a local browser and a loopback listener on the machine
running the process.

The token supplier form matters as much as the fixed-string form: a long-lived
process wants to hand over a *callable* so each request picks up a refreshed
token, rather than pinning one that expires.
"""

from __future__ import annotations

import pytest

from bcli.auth._static import StaticTokenAuth
from bcli.errors import ConfigError


class TestFixedToken:
    async def test_returns_the_token(self):
        auth = StaticTokenAuth("header.payload.signature")
        assert await auth.get_access_token() == "header.payload.signature"

    async def test_returns_the_same_token_on_repeated_calls(self):
        auth = StaticTokenAuth("tok")
        assert await auth.get_access_token() == "tok"
        assert await auth.get_access_token() == "tok"

    def test_rejects_an_empty_token(self):
        """Validate at the boundary — an empty bearer would fail as a confusing 401
        several layers away."""
        with pytest.raises(ConfigError, match="empty"):
            StaticTokenAuth("")

    def test_rejects_a_whitespace_only_token(self):
        with pytest.raises(ConfigError, match="empty"):
            StaticTokenAuth("   ")

    def test_strips_surrounding_whitespace(self):
        assert StaticTokenAuth("  tok\n")._token == "tok"

    def test_rejects_a_bearer_prefixed_token(self):
        """The transport adds `Bearer ` itself; accepting it here would produce
        `Authorization: Bearer Bearer …`."""
        with pytest.raises(ConfigError, match="Bearer"):
            StaticTokenAuth("Bearer tok")


class TestTokenSupplier:
    async def test_calls_an_async_supplier(self):
        async def supply() -> str:
            return "from-supplier"

        assert await StaticTokenAuth(supply).get_access_token() == "from-supplier"

    async def test_calls_a_sync_supplier(self):
        assert await StaticTokenAuth(lambda: "sync-token").get_access_token() == "sync-token"

    async def test_re_invokes_the_supplier_each_call_so_refreshes_are_picked_up(self):
        tokens = iter(["first", "second"])

        async def supply() -> str:
            return next(tokens)

        auth = StaticTokenAuth(supply)
        assert await auth.get_access_token() == "first"
        assert await auth.get_access_token() == "second"

    async def test_rejects_an_empty_token_from_the_supplier(self):
        with pytest.raises(ConfigError, match="empty"):
            await StaticTokenAuth(lambda: "").get_access_token()


class TestProtocolConformance:
    async def test_satisfies_the_auth_provider_shape(self):
        """Duck-typed against AuthProvider (bcli/auth/_base.py): an awaitable
        get_access_token and a synchronous clear_cache."""
        auth = StaticTokenAuth("tok")
        assert await auth.get_access_token() == "tok"
        assert auth.clear_cache() is None

    async def test_clear_cache_does_not_invalidate_the_supplied_token(self):
        """Nothing is cached here — the caller owns the token's lifetime. Callers
        that need invalidation should supply a callable instead."""
        auth = StaticTokenAuth("tok")
        auth.clear_cache()
        assert await auth.get_access_token() == "tok"

    def test_is_usable_as_a_transport_auth_provider(self):
        from bcli.client._transport import BCTransport

        auth = StaticTokenAuth("tok")
        transport = BCTransport(auth)
        assert transport._auth is auth
