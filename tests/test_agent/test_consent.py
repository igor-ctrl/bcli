"""Subscription-consent gate: fires only on subscription auth, persists."""

from __future__ import annotations

import pytest

from bcli.config._model import AgentConfig
from bcli_cli.repl import _consent


def test_no_consent_for_pydantic_ai() -> None:
    assert _consent.needs_consent(AgentConfig(backend="pydantic-ai")) is False


def test_no_consent_when_already_authorized() -> None:
    cfg = AgentConfig(backend="claude-code", subscription_authorized=True)
    assert _consent.needs_consent(cfg) is False


def test_consent_needed_only_on_subscription_auth(monkeypatch) -> None:
    cfg = AgentConfig(backend="claude-code")
    monkeypatch.setattr(_consent, "detect_claude_auth", lambda: "subscription", raising=False)
    # Patch the lazily-imported function at its source module too.
    import bcli.agent._auth_detect as ad

    monkeypatch.setattr(ad, "detect_claude_auth", lambda **_kw: "subscription")
    assert _consent.needs_consent(cfg) is True

    monkeypatch.setattr(ad, "detect_claude_auth", lambda **_kw: "api_key")
    assert _consent.needs_consent(cfg) is False


def test_ensure_consent_non_interactive_denies(monkeypatch) -> None:
    cfg = AgentConfig(backend="codex")
    import bcli.agent._auth_detect as ad

    monkeypatch.setattr(ad, "detect_codex_auth", lambda **_kw: "subscription")
    assert _consent.ensure_subscription_consent(cfg, interactive=False) is False


def test_ensure_consent_accepts_literal_yes_and_persists(monkeypatch) -> None:
    cfg = AgentConfig(backend="codex")
    import bcli.agent._auth_detect as ad

    monkeypatch.setattr(ad, "detect_codex_auth", lambda **_kw: "subscription")

    persisted = {}
    monkeypatch.setattr(_consent, "persist_consent", lambda: persisted.update(done=True))

    ok = _consent.ensure_subscription_consent(
        cfg, interactive=True, input_func=lambda _p: "yes",
    )
    assert ok is True
    assert persisted.get("done") is True


def test_ensure_consent_rejects_non_yes(monkeypatch) -> None:
    cfg = AgentConfig(backend="codex")
    import bcli.agent._auth_detect as ad

    monkeypatch.setattr(ad, "detect_codex_auth", lambda **_kw: "subscription")
    monkeypatch.setattr(_consent, "persist_consent", lambda: pytest.fail("should not persist"))

    ok = _consent.ensure_subscription_consent(
        cfg, interactive=True, input_func=lambda _p: "y",
    )
    assert ok is False
