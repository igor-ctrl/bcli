"""Shared fixtures for idempotency CLI-level tests.

Mirrors the ``cli_state`` pattern in ``tests/test_envelope/conftest.py``:
seed a writable Sandbox profile so ``confirm_write_or_exit`` —
unconditionally invoked from the mutation commands — can read
``state.profile`` without raising ``ConfigError`` in a hermetic
environment (no ``~/.config/bcli/config.toml``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Scope HOME / Path.home to tmp_path so any incidental ledger or
    cache write lands under the per-test tree rather than the
    developer's real ``~/.config/bcli/``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


@pytest.fixture
def writable_state(monkeypatch):
    """CLI state pointing at a writable Sandbox profile.

    Required because ``confirm_write_or_exit`` reads ``state.profile``
    even when ``yes=True``; a missing config raises ``ConfigError`` and
    breaks the test before the assertion runs.
    """
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
