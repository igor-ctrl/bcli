"""Regression tests for hourly re-authentication.

The defect: ``BrowserAuth`` / ``DeviceCodeAuth`` built
``msal.PublicClientApplication`` with no ``token_cache=``. MSAL's cache was
therefore in-memory only, so in a fresh process ``get_accounts()`` returned
``[]``, ``acquire_token_silent()`` was unreachable, and the user was pushed
through a full interactive flow every time the ~1h access token in
``TokenCache`` expired.

These tests assert the fix at the level the user actually feels it: a *second,
independently constructed* auth provider — standing in for the next CLI
invocation — renews from the persisted refresh token and never prompts.

Placeholder identifiers only; never real tenant or client values.
"""

from __future__ import annotations

import asyncio

import pytest

import bcli.auth._browser as browser_mod
import bcli.auth._device_code as device_mod
from bcli.auth._browser import BrowserAuth
from bcli.auth._device_code import DeviceCodeAuth
from bcli.auth._msal_cache import MsalTokenCache

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"

_TOKEN_RESPONSE = {
    "client_id": CLIENT,
    "scope": ["https://example.invalid/.default"],
    "token_endpoint": (
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    ),
    "response": {
        "access_token": "placeholder-interactive-token",
        "refresh_token": "placeholder-refresh-token",
        "token_type": "Bearer",
        "expires_in": 3600,
        "id_token_claims": {
            "sub": "44444444-4444-4444-4444-444444444444",
            "preferred_username": "placeholder@example.invalid",
            "tid": TENANT,
        },
    },
}


class _NullAccessTokenCache:
    """bcli's own access-token cache, always cold, so the MSAL path is used."""

    def __init__(self) -> None:
        self.puts: list[str] = []

    def get(self, tenant_id: str, client_id: str) -> None:
        return None

    def put(self, tenant_id: str, client_id: str, access_token: str, expires_in: int) -> None:
        self.puts.append(access_token)

    def clear(self, *args: object, **kwargs: object) -> None:
        pass


class _FakeMSALApp:
    """Minimal stand-in that honours the token_cache it is handed.

    Mirrors the two MSAL behaviours under test: an account exists only if one
    was persisted, and the interactive flow writes into the cache.
    """

    interactive_calls = 0

    def __init__(
        self,
        client_id: str | None = None,
        authority: str | None = None,
        token_cache: object | None = None,
        **kwargs: object,
    ) -> None:
        self.token_cache = token_cache

    def get_accounts(self) -> list[dict]:
        if self.token_cache is None:
            return []
        if "placeholder-refresh-token" in self.token_cache.serialize():
            return [{"username": "placeholder@example.invalid"}]
        return []

    def acquire_token_silent(self, scopes, account):
        return {"access_token": "placeholder-silent-token", "expires_in": 3600}

    # ── interactive paths ────────────────────────────────────────────────
    def initiate_device_flow(self, scopes):
        return {"user_code": "PLACEHOLDER", "message": "placeholder message"}

    def acquire_token_by_device_flow(self, flow):
        return self._interactive()

    def initiate_auth_code_flow(self, scopes, redirect_uri, **kwargs):
        return {"auth_uri": "http://example.invalid/auth", "state": "S"}

    def acquire_token_by_auth_code_flow(self, flow, response):
        return self._interactive()

    def _interactive(self) -> dict:
        type(self).interactive_calls += 1
        # Mimic MSAL populating its own cache during acquisition.
        if self.token_cache is not None:
            self.token_cache.add(_TOKEN_RESPONSE)
        return {"access_token": "placeholder-interactive-token", "expires_in": 3600}


@pytest.fixture(autouse=True)
def _reset_counter():
    _FakeMSALApp.interactive_calls = 0
    yield


# ── Device code ───────────────────────────────────────────────────────────


def test_device_code_second_invocation_renews_without_prompting(tmp_path, monkeypatch):
    monkeypatch.setattr(device_mod.msal, "PublicClientApplication", _FakeMSALApp)
    cache_file = tmp_path / "msal_cache.json"

    # First invocation: nothing cached, so the interactive flow runs and the
    # refresh token lands on disk.
    first = DeviceCodeAuth(
        tenant_id=TENANT,
        client_id=CLIENT,
        token_cache=_NullAccessTokenCache(),
        msal_cache=MsalTokenCache(cache_file=cache_file),
    )
    token = asyncio.run(first.get_access_token())

    assert token == "placeholder-interactive-token"
    assert _FakeMSALApp.interactive_calls == 1
    assert cache_file.exists(), "refresh token was not persisted"

    # Second invocation — a brand-new provider, as a separate CLI run would
    # build. This is the case that used to force another prompt.
    second = DeviceCodeAuth(
        tenant_id=TENANT,
        client_id=CLIENT,
        token_cache=_NullAccessTokenCache(),
        msal_cache=MsalTokenCache(cache_file=cache_file),
    )
    token2 = asyncio.run(second.get_access_token())

    assert token2 == "placeholder-silent-token"
    assert _FakeMSALApp.interactive_calls == 1, "second run prompted the user again"


# ── Browser ───────────────────────────────────────────────────────────────


def test_browser_second_invocation_renews_without_opening_a_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_mod.msal, "PublicClientApplication", _FakeMSALApp)
    opened: list[str] = []
    monkeypatch.setattr(
        browser_mod, "_open_browser", lambda url, **kw: opened.append(url)
    )
    cache_file = tmp_path / "msal_cache.json"

    # Seed the cache as a prior interactive login would have.
    seed = MsalTokenCache(cache_file=cache_file)
    seed.cache.add(_TOKEN_RESPONSE)
    seed.save()

    auth = BrowserAuth(
        tenant_id=TENANT,
        client_id=CLIENT,
        token_cache=_NullAccessTokenCache(),
        msal_cache=MsalTokenCache(cache_file=cache_file),
    )
    token = asyncio.run(auth.get_access_token())

    assert token == "placeholder-silent-token"
    assert opened == [], "browser was opened despite a usable refresh token"
    assert _FakeMSALApp.interactive_calls == 0


def test_browser_passes_a_persistent_cache_to_msal(tmp_path, monkeypatch):
    """The whole fix hinges on this argument being present."""
    captured: dict[str, object] = {}

    class _Capturing(_FakeMSALApp):
        def __init__(self, *args, **kwargs):
            captured["token_cache"] = kwargs.get("token_cache")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(browser_mod.msal, "PublicClientApplication", _Capturing)
    monkeypatch.setattr(browser_mod, "_open_browser", lambda *a, **kw: None)

    store = MsalTokenCache(cache_file=tmp_path / "msal_cache.json")
    store.cache.add(_TOKEN_RESPONSE)

    auth = BrowserAuth(
        tenant_id=TENANT,
        client_id=CLIENT,
        token_cache=_NullAccessTokenCache(),
        msal_cache=store,
    )
    asyncio.run(auth.get_access_token())

    assert captured["token_cache"] is store.cache


# ── Logout must actually log out ──────────────────────────────────────────


def test_clear_cache_removes_the_refresh_token(tmp_path, monkeypatch):
    """Clearing only the access token would leave silent renewal working."""
    monkeypatch.setattr(device_mod.msal, "PublicClientApplication", _FakeMSALApp)
    cache_file = tmp_path / "msal_cache.json"

    store = MsalTokenCache(cache_file=cache_file)
    store.cache.add(_TOKEN_RESPONSE)
    store.save()
    assert "placeholder-refresh-token" in cache_file.read_text(encoding="utf-8")

    auth = DeviceCodeAuth(
        tenant_id=TENANT,
        client_id=CLIENT,
        token_cache=_NullAccessTokenCache(),
        msal_cache=MsalTokenCache(cache_file=cache_file),
    )
    auth.clear_cache()

    remaining = MsalTokenCache(cache_file=cache_file).cache.serialize()
    assert "placeholder-refresh-token" not in remaining
