"""Wizard: end-to-end config write with mocked keychain + config IO."""

from __future__ import annotations

import bcli_cli.repl._wizard as wizard


def test_wizard_writes_local_backend_section(monkeypatch) -> None:
    """Choice 3 (local model, no key) writes backend + base_url, no keychain."""
    written = {}
    monkeypatch.setattr(
        "bcli.config._loader.update_config_section",
        lambda section, values: written.update({section: values}),
    )

    # Drive the prompts: pick 3, accept base_url default, accept model default.
    answers = iter(["3", "http://localhost:11434/v1", "ollama:llama3.1"])
    monkeypatch.setattr(wizard, "_ask", lambda *a, **k: next(answers))

    ok = wizard.run_setup_wizard(force=True, input_func=lambda _p: next(answers))
    assert ok is True
    assert written["agent"]["backend"] == "pydantic-ai"
    assert written["agent"]["base_url"] == "http://localhost:11434/v1"


def test_wizard_stores_api_key_in_keychain(monkeypatch) -> None:
    written = {}
    stored = {}
    monkeypatch.setattr(
        "bcli.config._loader.update_config_section",
        lambda section, values: written.update({section: values}),
    )
    monkeypatch.setattr(
        "bcli.agent.backends._pydantic_ai.store_llm_key",
        lambda provider, key: stored.update({provider: key}) or True,
    )

    answers = iter(["1", "sk-test-123"])
    monkeypatch.setattr(wizard, "_ask", lambda *a, **k: next(answers))

    ok = wizard.run_setup_wizard(force=True)
    assert ok is True
    assert written["agent"]["backend"] == "pydantic-ai"
    assert written["agent"]["model"] == "anthropic:claude-sonnet-4-5"
    assert stored["anthropic"] == "sk-test-123"


def test_wizard_aborts_on_unknown_choice(monkeypatch) -> None:
    monkeypatch.setattr(
        "bcli.config._loader.update_config_section",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not write")),
    )
    monkeypatch.setattr(wizard, "_ask", lambda *a, **k: "99")
    assert wizard.run_setup_wizard(force=True) is False
