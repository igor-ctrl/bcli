"""Bare-``bcli`` dispatch: non-TTY → help (regression), TTY → REPL launch.

The contract that protects every scripted/piped caller of bcli: a bare
invocation with no subcommand must still print help and exit 0 when
stdout/stdin aren't both TTYs. Only an interactive terminal opens the
chat REPL.
"""

from __future__ import annotations

import bcli_cli.app as app_mod
from bcli_cli.app import app
from typer.testing import CliRunner

runner = CliRunner()


def test_bare_bcli_non_tty_prints_help() -> None:
    # CliRunner pipes stdio → not a TTY → help path.
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage" in result.output
    assert "Business Central" in result.output


def test_bare_bcli_tty_launches_repl(monkeypatch) -> None:
    monkeypatch.setattr(app_mod, "_stdio_is_tty", lambda: True)
    launched = {}

    def fake_launch(*, profile=None):
        launched["profile"] = profile
        return 0

    # The lazy import resolves bcli_cli.repl.launch_repl.
    import bcli_cli.repl as repl_mod

    monkeypatch.setattr(repl_mod, "launch_repl", fake_launch)

    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "profile" in launched


def test_bare_bcli_tty_passes_profile(monkeypatch) -> None:
    monkeypatch.setattr(app_mod, "_stdio_is_tty", lambda: True)
    seen = {}
    import bcli_cli.repl as repl_mod

    monkeypatch.setattr(repl_mod, "launch_repl", lambda *, profile=None: seen.update(p=profile) or 0)

    result = runner.invoke(app, ["--profile", "finance"])
    assert result.exit_code == 0
    assert seen["p"] == "finance"


def test_subcommand_unaffected_by_bare_branch() -> None:
    # A real subcommand must never trigger the REPL branch.
    result = runner.invoke(app, ["endpoint", "--help"])
    assert result.exit_code == 0
    assert "search" in result.output
