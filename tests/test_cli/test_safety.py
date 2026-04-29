"""Tests for confirm_write_or_exit — CLI write-safety prompt."""

from __future__ import annotations

import pytest
import typer

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._safety import confirm_write_or_exit
from bcli_cli._state import state


@pytest.fixture
def writable_profile(monkeypatch):
    """Active profile that allows writes — confirm_write_or_exit must no-op."""
    cfg = BCConfig(
        defaults=BCDefaults(profile="writable"),
        profiles={
            "writable": BCProfile(
                tenant_id="t1", environment="Sandbox", company_id="c-1",
                disable_writes=False,
            ),
        },
    )
    state._config = cfg
    state._registry = None
    state.profile_name = None
    yield
    state._config = None
    state._registry = None


@pytest.fixture
def readonly_profile(monkeypatch):
    """Active profile is read-only — confirm_write_or_exit must guard."""
    cfg = BCConfig(
        defaults=BCDefaults(profile="readonly"),
        profiles={
            "readonly": BCProfile(
                tenant_id="t1", environment="Production", company_id="c-1",
                disable_writes=True,
            ),
        },
    )
    state._config = cfg
    state._registry = None
    state.profile_name = None
    yield
    state._config = None
    state._registry = None


class TestWritableProfile:
    def test_no_prompt_when_writes_allowed(self, writable_profile):
        # Should return cleanly with zero output / interaction
        confirm_write_or_exit("POST", "vendors", yes=False)


class TestReadonlyProfile:
    def test_yes_flag_proceeds_with_warning(self, readonly_profile, capsys):
        confirm_write_or_exit("DELETE", "vendors", yes=True)
        err = capsys.readouterr().err
        assert "read-only" in err.lower()
        assert "DELETE" in err
        assert "vendors" in err

    def test_non_interactive_without_yes_aborts(self, readonly_profile, monkeypatch):
        # Force isatty() to False on stdin
        import sys
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        with pytest.raises(typer.Exit) as excinfo:
            confirm_write_or_exit("POST", "vendors", yes=False)
        assert excinfo.value.exit_code == 1

    def test_interactive_yes_input_proceeds(self, readonly_profile, monkeypatch):
        # Pretend stdin is a TTY and the user types 'yes'
        import sys
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("typer.prompt", lambda *a, **kw: "yes")
        confirm_write_or_exit("POST", "vendors", yes=False)  # should not raise

    def test_interactive_yes_case_insensitive(self, readonly_profile, monkeypatch):
        import sys
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("typer.prompt", lambda *a, **kw: "  YES  ")
        confirm_write_or_exit("POST", "vendors", yes=False)

    def test_interactive_anything_else_aborts(self, readonly_profile, monkeypatch):
        import sys
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("typer.prompt", lambda *a, **kw: "y")  # not literal 'yes'
        with pytest.raises(typer.Exit) as excinfo:
            confirm_write_or_exit("POST", "vendors", yes=False)
        assert excinfo.value.exit_code == 1

    def test_empty_input_aborts(self, readonly_profile, monkeypatch):
        import sys
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("typer.prompt", lambda *a, **kw: "")
        with pytest.raises(typer.Exit):
            confirm_write_or_exit("POST", "vendors", yes=False)
