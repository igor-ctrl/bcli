"""Tests for the persistent MSAL token cache.

Before this cache existed, ``BrowserAuth`` / ``DeviceCodeAuth`` built
``msal.PublicClientApplication`` with no ``token_cache=`` argument. MSAL then
kept its cache in memory only, so:

  * ``app.get_accounts()`` was always empty in a fresh process;
  * ``acquire_token_silent()`` could therefore never return anything;
  * only the raw access token was persisted (``TokenCache``, ~60-75 min TTL).

Net effect: a full interactive re-auth roughly every hour. Persisting MSAL's
own cache keeps the refresh token, so silent renewal works across invocations.

These tests use placeholder tenant/client identifiers only — never real ones.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from bcli.auth._msal_cache import MsalTokenCache

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
AUTHORITY = f"https://login.microsoftonline.com/{TENANT}"


# ── Round-tripping ────────────────────────────────────────────────────────


def test_load_returns_empty_cache_when_file_absent(tmp_path):
    store = MsalTokenCache(cache_file=tmp_path / "msal_cache.json")
    # A brand-new cache has no state to serialize.
    assert store.cache.serialize() in ("", "{}")


def test_save_is_a_noop_when_nothing_changed(tmp_path):
    path = tmp_path / "msal_cache.json"
    store = MsalTokenCache(cache_file=path)

    # Touching nothing means has_state_changed is False — don't write.
    assert store.save() is False
    assert not path.exists()


def test_save_writes_when_state_changed_and_reload_sees_it(tmp_path):
    path = tmp_path / "msal_cache.json"
    store = MsalTokenCache(cache_file=path)

    # Drive MSAL's own cache API rather than hand-rolling its JSON shape.
    store.cache.add(
        {
            "client_id": CLIENT,
            "scope": ["https://example.invalid/.default"],
            "token_endpoint": f"{AUTHORITY}/oauth2/v2.0/token",
            "response": {
                "access_token": "placeholder-access-token",
                "refresh_token": "placeholder-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        }
    )

    assert store.save() is True
    assert path.exists()

    # A second store pointed at the same file must see the refresh token.
    reloaded = MsalTokenCache(cache_file=path)
    blob = reloaded.cache.serialize()
    assert "placeholder-refresh-token" in blob


def test_persisted_file_is_private(tmp_path):
    """The cache holds a refresh token — it must not be world/group readable."""
    if os.name != "posix":
        pytest.skip("POSIX permission semantics only")

    path = tmp_path / "msal_cache.json"
    store = MsalTokenCache(cache_file=path)
    store.cache.add(
        {
            "client_id": CLIENT,
            "scope": ["https://example.invalid/.default"],
            "token_endpoint": f"{AUTHORITY}/oauth2/v2.0/token",
            "response": {
                "access_token": "placeholder-access-token",
                "refresh_token": "placeholder-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        }
    )
    store.save()

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & 0o077 == 0, f"cache file mode {oct(mode)} is group/other accessible"


def test_corrupt_cache_file_does_not_raise(tmp_path):
    """A truncated or hand-edited cache must degrade to re-auth, not crash."""
    path = tmp_path / "msal_cache.json"
    path.write_text("{not valid json", encoding="utf-8")

    store = MsalTokenCache(cache_file=path)
    # Must not raise; an unusable cache is simply an empty one.
    assert store.cache is not None


# ── Clearing ──────────────────────────────────────────────────────────────


def test_clear_removes_the_file(tmp_path):
    path = tmp_path / "msal_cache.json"
    path.write_text("{}", encoding="utf-8")

    store = MsalTokenCache(cache_file=path)
    store.clear()

    assert not path.exists()


def test_clear_is_idempotent_when_file_missing(tmp_path):
    store = MsalTokenCache(cache_file=tmp_path / "nope.json")
    store.clear()  # must not raise


def test_remove_accounts_drops_persisted_refresh_token(tmp_path):
    """logout must actually invalidate locally, not just drop the access token."""
    path = tmp_path / "msal_cache.json"
    store = MsalTokenCache(cache_file=path)
    store.cache.add(
        {
            "client_id": CLIENT,
            "scope": ["https://example.invalid/.default"],
            "token_endpoint": f"{AUTHORITY}/oauth2/v2.0/token",
            "response": {
                "access_token": "placeholder-access-token",
                "refresh_token": "placeholder-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token_claims": {
                    # MSAL requires "sub" (OIDC guarantees it) to derive the
                    # home account id when client_info is absent.
                    "sub": "44444444-4444-4444-4444-444444444444",
                    "preferred_username": "placeholder@example.invalid",
                    "oid": "33333333-3333-3333-3333-333333333333",
                    "tid": TENANT,
                },
            },
        }
    )
    store.save()

    store.remove_accounts(client_id=CLIENT, authority=AUTHORITY)

    reloaded = MsalTokenCache(cache_file=path)
    assert "placeholder-refresh-token" not in reloaded.cache.serialize()


def test_default_path_lives_under_the_config_dir():
    from bcli.config._defaults import CONFIG_DIR, MSAL_CACHE_FILE

    assert MSAL_CACHE_FILE.parent == CONFIG_DIR
    # Must not collide with the access-token cache.
    from bcli.config._defaults import TOKEN_CACHE_FILE

    assert MSAL_CACHE_FILE != TOKEN_CACHE_FILE


def test_save_emits_valid_json(tmp_path):
    """Other tooling (and our own reload) must be able to parse the artefact."""
    path = tmp_path / "msal_cache.json"
    store = MsalTokenCache(cache_file=path)
    store.cache.add(
        {
            "client_id": CLIENT,
            "scope": ["https://example.invalid/.default"],
            "token_endpoint": f"{AUTHORITY}/oauth2/v2.0/token",
            "response": {
                "access_token": "placeholder-access-token",
                "refresh_token": "placeholder-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        }
    )
    store.save()

    json.loads(path.read_text(encoding="utf-8"))  # must not raise
