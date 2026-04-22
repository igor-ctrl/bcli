"""Tests for bcli company aliases-import."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from bcli.config._loader import load_config, save_config
from bcli.config._model import BCConfig, BCDefaults, BCProfile, CompanyAlias
from bcli_cli._state import state
from bcli_cli.app import app

runner = CliRunner()


@pytest.fixture
def tmp_config(monkeypatch, tmp_path):
    """Redirect the global config file and reset the state singleton."""
    config_dir = tmp_path / "bcli"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"

    monkeypatch.setattr("bcli.config._loader.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._loader.CONFIG_DIR", config_dir)
    monkeypatch.setattr("bcli_cli.commands.config_cmd.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._loader._find_project_config", lambda: None)
    for env_var in ("BCLI_PROFILE", "BCLI_FORMAT", "BCLI_TIMEOUT"):
        monkeypatch.delenv(env_var, raising=False)

    state._config = None
    state._registry = None
    state.profile_name = None
    yield config_file
    state._config = None
    state._registry = None
    state.profile_name = None


def _write_two_profiles(target_aliases: dict[str, CompanyAlias] | None = None) -> None:
    """Seed a config with a 'sandbox' (source) and a 'prod' (target) profile."""
    cfg = BCConfig(
        defaults=BCDefaults(profile="prod"),
        profiles={
            "sandbox": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                companies={
                    "LLC": CompanyAlias(id="c-llc", name="Contoso Ltd"),
                    "Corp": CompanyAlias(id="c-corp", name="Northwind"),
                },
            ),
            "prod": BCProfile(
                tenant_id="t1",
                environment="Production",
                companies=target_aliases or {},
            ),
        },
    )
    save_config(cfg)


def test_aliases_import_copies_to_empty_target(tmp_config):
    _write_two_profiles()

    result = runner.invoke(
        app, ["--profile", "prod", "company", "aliases-import", "--from", "sandbox"]
    )
    assert result.exit_code == 0, result.stdout
    assert "Wrote 2 alias" in result.stdout

    reloaded = load_config()
    prod = reloaded.get_profile("prod")
    assert set(prod.companies.keys()) == {"LLC", "Corp"}
    assert prod.companies["LLC"].id == "c-llc"
    assert prod.companies["Corp"].name == "Northwind"


def test_aliases_import_skips_existing_without_overwrite(tmp_config):
    _write_two_profiles(
        target_aliases={"LLC": CompanyAlias(id="different-id", name="prod-only")}
    )

    result = runner.invoke(
        app, ["--profile", "prod", "company", "aliases-import", "--from", "sandbox"]
    )
    assert result.exit_code == 0, result.stdout

    reloaded = load_config()
    prod = reloaded.get_profile("prod")
    # LLC was skipped → keeps the original target value
    assert prod.companies["LLC"].id == "different-id"
    # Corp was added fresh
    assert prod.companies["Corp"].id == "c-corp"


def test_aliases_import_overwrite_replaces_existing(tmp_config):
    _write_two_profiles(
        target_aliases={"LLC": CompanyAlias(id="different-id", name="prod-only")}
    )

    result = runner.invoke(
        app,
        [
            "--profile", "prod",
            "company", "aliases-import",
            "--from", "sandbox",
            "--overwrite",
        ],
    )
    assert result.exit_code == 0, result.stdout

    reloaded = load_config()
    prod = reloaded.get_profile("prod")
    # LLC was overwritten
    assert prod.companies["LLC"].id == "c-llc"
    assert prod.companies["LLC"].name == "Contoso Ltd"


def test_aliases_import_dry_run_does_not_write(tmp_config):
    _write_two_profiles()
    before = tmp_config.read_bytes()

    result = runner.invoke(
        app,
        [
            "--profile", "prod",
            "company", "aliases-import",
            "--from", "sandbox",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "dry-run" in result.stdout.lower()
    # Config file untouched byte-for-byte
    assert tmp_config.read_bytes() == before


def test_aliases_import_same_profile_rejected(tmp_config):
    _write_two_profiles()

    result = runner.invoke(
        app, ["--profile", "prod", "company", "aliases-import", "--from", "prod"]
    )
    assert result.exit_code == 1
    assert "same profile" in result.stdout.lower()


def test_aliases_import_missing_source_rejected(tmp_config):
    _write_two_profiles()

    result = runner.invoke(
        app,
        ["--profile", "prod", "company", "aliases-import", "--from", "nonexistent"],
    )
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_aliases_import_empty_source_is_noop(tmp_config):
    cfg = BCConfig(
        defaults=BCDefaults(profile="prod"),
        profiles={
            "sandbox": BCProfile(tenant_id="t1", environment="Sandbox"),
            "prod": BCProfile(tenant_id="t1", environment="Production"),
        },
    )
    save_config(cfg)

    result = runner.invoke(
        app, ["--profile", "prod", "company", "aliases-import", "--from", "sandbox"]
    )
    assert result.exit_code == 0
    assert "no aliases" in result.stdout.lower()
