"""Tests for format auto-detection — agents, non-TTYs, Windows fallback."""

from __future__ import annotations

import pytest

from bcli_cli.output._formatters import detect_default_format


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Clear all the env vars detect_default_format checks so each test
    starts from a known baseline."""
    for key in ("BCLI_FORMAT", "CLAUDECODE", "BCLI_AGENT", "WT_SESSION"):
        monkeypatch.delenv(key, raising=False)


def _force_tty(monkeypatch, is_tty: bool) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: is_tty)


class TestDefaultFormat:
    def test_explicit_bcli_format_env_wins(self, monkeypatch):
        monkeypatch.setenv("BCLI_FORMAT", "csv")
        _force_tty(monkeypatch, True)
        assert detect_default_format() == "csv"

    def test_claude_code_gets_markdown(self, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "1")
        _force_tty(monkeypatch, True)
        assert detect_default_format() == "markdown"

    def test_generic_agent_gets_markdown(self, monkeypatch):
        monkeypatch.setenv("BCLI_AGENT", "1")
        _force_tty(monkeypatch, True)
        assert detect_default_format() == "markdown"

    def test_non_tty_gets_markdown(self, monkeypatch):
        _force_tty(monkeypatch, False)
        assert detect_default_format() == "markdown"

    def test_posix_tty_gets_table(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        _force_tty(monkeypatch, True)
        assert detect_default_format() == "table"


class TestWindowsLegacyConsole:
    """Classic PowerShell / conhost.exe pretends to be a TTY even when
    rich's box-drawing renders as `�` mojibake. We default to markdown
    there and only switch to table when we can prove the terminal can
    handle it (Windows Terminal sets WT_SESSION)."""

    def test_windows_legacy_console_uses_markdown(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        _force_tty(monkeypatch, True)
        # No WT_SESSION = classic console
        assert detect_default_format() == "markdown"

    def test_windows_terminal_keeps_table(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("WT_SESSION", "deadbeef-1234")
        _force_tty(monkeypatch, True)
        assert detect_default_format() == "table"

    def test_explicit_table_format_overrides_windows_safety_net(self, monkeypatch):
        # Power user knows what they're doing: BCLI_FORMAT=table on classic
        # PowerShell still gives them table. They can fix their codepage.
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("BCLI_FORMAT", "table")
        _force_tty(monkeypatch, True)
        assert detect_default_format() == "table"
