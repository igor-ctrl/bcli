"""``detect_default_format`` should default to JSON when stdout isn't a TTY.

AIP §Phase 4b: piped/redirected stdout means a programmatic consumer is
reading the output. JSON is the canonical machine-readable shape — agents
shouldn't have to spell ``--format json`` to get it.

The CLAUDECODE / BCLI_AGENT env-var branches are *explicit* user opt-ins
for markdown and are left alone. The win32 mojibake branch likewise
stays as ``markdown`` since the issue is rendering, not parseability.
"""

from __future__ import annotations

import sys

import pytest

from bcli_cli.output import detect_default_format


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Clear env-vars that would short-circuit the detection."""
    for var in ("BCLI_FORMAT", "CLAUDECODE", "BCLI_AGENT"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_non_tty_defaults_to_json(monkeypatch):
    """Pipe / redirect → JSON (the Phase 4b standardization)."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert detect_default_format() == "json"


def test_tty_keeps_table(monkeypatch):
    """Interactive shell still gets the rich table."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    assert detect_default_format() == "table"


def test_bcli_format_env_overrides_pipe(monkeypatch):
    """Explicit env-var beats any auto-detection."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setenv("BCLI_FORMAT", "csv")
    assert detect_default_format() == "csv"


def test_claudecode_env_still_picks_markdown(monkeypatch):
    """Explicit AI-agent hint keeps its existing semantics (markdown).

    Flipping this would also be a Phase-4-like standardization, but the
    task scopes 4b to the non-TTY branch only — agents that want JSON
    pass ``--format json`` like everyone else.
    """
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert detect_default_format() == "markdown"


def test_bcli_agent_env_still_picks_markdown(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setenv("BCLI_AGENT", "1")
    assert detect_default_format() == "markdown"


def test_windows_legacy_console_keeps_markdown(monkeypatch):
    """conhost.exe still renders rich's UTF-8 box-drawing as `?`.

    JSON is *parseable* there but markdown stays a strictly safer
    rendering choice for an interactive console — flipping to JSON
    would surprise users who run ``bcli get`` in a normal cmd window.
    """
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("WT_SESSION", raising=False)
    assert detect_default_format() == "markdown"


def test_windows_terminal_still_gets_table(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("WT_SESSION", "abc123")
    assert detect_default_format() == "table"
