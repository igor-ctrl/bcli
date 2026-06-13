"""Headless ``bcli agent run`` + plan-mode resolution."""

from __future__ import annotations

from typer.testing import CliRunner

from bcli_cli.app import app
from bcli_cli.commands.agent_cmd import resolve_plan_mode

runner = CliRunner()


def test_plan_mode_auto_on_for_production() -> None:
    assert resolve_plan_mode("auto", is_production=True) is True
    assert resolve_plan_mode("auto", is_production=False) is False


def test_plan_mode_explicit_flags_win() -> None:
    assert resolve_plan_mode("off", is_production=True, force_on=True) is True
    assert resolve_plan_mode("on", is_production=False, force_off=True) is False


def test_plan_mode_on_off_strings() -> None:
    assert resolve_plan_mode("on", is_production=False) is True
    assert resolve_plan_mode("off", is_production=True) is False


def test_agent_run_with_null_backend_exits_nonzero() -> None:
    """No backend configured → setup hint on stderr, exit 1."""
    result = runner.invoke(app, ["agent", "run", "hello"])
    assert result.exit_code == 1
    # NullAgentBackend setup hint is printed (combined stdout+stderr).
    assert "backend" in result.output.lower()


def test_agent_subcommands_registered() -> None:
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "init" in result.output
