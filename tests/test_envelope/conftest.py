"""Shared envelope-tests fixtures.

Sets up a writable, sandboxed profile and stubs ``state.make_async_client``
so the mutation commands can run end-to-end against a fake transport.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state


@pytest.fixture
def cli_state(monkeypatch):
    """Active CLI state pointing at a writable Sandbox profile."""
    cfg = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={
            "dev": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                company_id="c-123",
                disable_writes=False,
            ),
        },
    )
    state._config = cfg
    state._registry = None
    state._telemetry = None
    state.profile_name = None
    state.env_override = None
    state.company_override = None
    state.format = "table"
    state.dry_run = False
    state.quiet = False
    yield state
    state._config = None
    state._registry = None
    state._telemetry = None
    state.profile_name = None
    state.dry_run = False


@pytest.fixture
def fake_client():
    """Async client with mockable post/patch/delete/upload_attachment.

    Default success returns include a ``systemId`` so the envelope can
    pick a record_id; tests can override per-test.
    """
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.post = AsyncMock(return_value={"systemId": "vnd-9", "displayName": "Acme"})
    c.patch = AsyncMock(return_value={"systemId": "vnd-9", "displayName": "Acme2"})
    c.delete = AsyncMock(return_value=None)
    c.upload_attachment = AsyncMock(
        return_value={"id": "att-9", "fileName": "x.pdf", "byteSize": 12}
    )
    c._resolve_url = lambda entity, **kw: f"https://example.test/{entity}"
    return c


@pytest.fixture
def stub_client(cli_state, fake_client, monkeypatch):
    """Patch state.make_async_client to return the fake client."""
    monkeypatch.setattr(state, "make_async_client", lambda **_: fake_client)
    return fake_client


@pytest.fixture
def stub_resolve_url(monkeypatch):
    """Stub ``try_resolve_url`` so we don't need a fully-wired client to
    capture the URL for dry-run / failure tests."""
    def _fake(endpoint, **kw):
        rid = kw.get("record_id")
        suffix = f"({rid})" if rid else ""
        return f"https://example.test/{endpoint}{suffix}"
    monkeypatch.setattr("bcli_cli._url_resolve.try_resolve_url", _fake)
    yield _fake


def write_yaml_file(tmp_path: Path, name: str, content: str) -> Path:
    import textwrap
    f = tmp_path / name
    f.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return f
