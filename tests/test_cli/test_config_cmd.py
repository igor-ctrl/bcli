"""Tests for bcli config path / config edit commands."""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from bcli_cli._state import state
from bcli_cli.app import app

runner = CliRunner()


@pytest.fixture
def tmp_config(monkeypatch, tmp_path):
    """Redirect the global config file to a tmp path and reset the state singleton."""
    config_dir = tmp_path / "bcli"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"

    # Patch both the loader (read/write) and the config_cmd module (direct refs).
    monkeypatch.setattr("bcli.config._loader.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._loader.CONFIG_DIR", config_dir)
    monkeypatch.setattr("bcli_cli.commands.config_cmd.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._loader._find_project_config", lambda: None)
    for env_var in ("BCLI_PROFILE", "BCLI_FORMAT", "BCLI_TIMEOUT"):
        monkeypatch.delenv(env_var, raising=False)

    # Reset state singleton so tests don't share cached config.
    state._config = None
    state._registry = None
    state.profile_name = None
    yield config_file
    state._config = None
    state._registry = None
    state.profile_name = None


def test_config_path_prints_file_path(tmp_config):
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert str(tmp_config) in result.stdout


def test_config_path_works_even_if_file_missing(tmp_config):
    """config path should print the path even when the file doesn't exist yet."""
    assert not tmp_config.exists()
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert str(tmp_config) in result.stdout


def test_config_edit_errors_when_file_missing(tmp_config):
    assert not tmp_config.exists()
    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 1
    assert "No config found" in result.stdout


def test_config_edit_success_path(tmp_config, monkeypatch):
    """config edit opens $EDITOR and re-validates on a valid config."""
    tmp_config.write_text(
        '[defaults]\nprofile = "test"\n\n'
        '[profiles.test]\ntenant_id = "t1"\nenvironment = "Sandbox"\n'
    )

    calls: list[list[str]] = []

    def fake_run(cmd, check):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("bcli_cli.commands.config_cmd.subprocess.run", fake_run)
    monkeypatch.setenv("EDITOR", "nano")

    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 0, result.stdout
    assert len(calls) == 1
    assert calls[0][0] == "nano"
    assert calls[0][1] == str(tmp_config)
    assert "Config saved and valid" in result.stdout


def test_config_edit_reports_invalid_toml(tmp_config, monkeypatch):
    """If the editor leaves the config in a bad state, we exit non-zero."""
    tmp_config.write_text(
        '[defaults]\nprofile = "test"\n\n'
        '[profiles.test]\ntenant_id = "t1"\nenvironment = "Sandbox"\n'
    )

    def corrupt_run(cmd, check):
        # Simulate the user saving a profile missing required fields.
        tmp_config.write_text(
            '[defaults]\nprofile = "test"\n\n'
            '[profiles.test]\n# missing tenant_id and environment\n'
        )
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("bcli_cli.commands.config_cmd.subprocess.run", corrupt_run)

    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 1
    assert "Config is now invalid" in result.stdout


def test_config_edit_reports_missing_editor(tmp_config, monkeypatch):
    tmp_config.write_text(
        '[defaults]\nprofile = "test"\n\n'
        '[profiles.test]\ntenant_id = "t1"\nenvironment = "Sandbox"\n'
    )

    def missing_editor(cmd, check):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr("bcli_cli.commands.config_cmd.subprocess.run", missing_editor)
    monkeypatch.setenv("EDITOR", "nonexistent-editor-xyz")

    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 1
    assert "not found" in result.stdout
