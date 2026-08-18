"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_msal_cache(tmp_path, monkeypatch):
    """Keep the persistent MSAL cache out of the developer's real config dir.

    ``MsalTokenCache`` defaults to ``~/.config/bcli/msal_cache.json``, which
    holds a real refresh token on any machine where bcli has been used. A test
    that constructs ``BrowserAuth`` / ``DeviceCodeAuth`` without an explicit
    ``msal_cache`` would otherwise read — and on save, overwrite — it.

    Autouse so no future test has to remember. Pointing the module-level
    default at ``tmp_path`` is enough; ``MsalTokenCache`` resolves it lazily
    per instance.
    """
    import bcli.auth._msal_cache as msal_cache_mod

    monkeypatch.setattr(
        msal_cache_mod, "MSAL_CACHE_FILE", tmp_path / "msal_cache.json"
    )
    yield
