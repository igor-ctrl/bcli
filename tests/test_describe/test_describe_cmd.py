"""End-to-end tests for `bcli describe` (AIP v0.1 Phase 1).

The command projects the live Typer app + EndpointRegistry + active
BCProfile as a single JSON document. These tests assert the schema
shape, forward-compat flags, cache behavior, and subtree mode.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bcli._version import __version__
from bcli_cli._state import state
from bcli_cli.app import app

runner = CliRunner()


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tmp_config(monkeypatch, tmp_path):
    """Redirect global config + registries + describe cache to a tmp tree.

    Mirrors the pattern in test_config_cmd.py. Resets the singleton state
    so cached config/registry from previous tests doesn't bleed in.
    """
    config_dir = tmp_path / "bcli"
    config_dir.mkdir()
    registries_dir = config_dir / "registries"
    registries_dir.mkdir()
    describe_dir = config_dir / "describe"  # describe cache lives under here
    config_file = config_dir / "config.toml"

    monkeypatch.setattr("bcli.config._loader.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._loader.CONFIG_DIR", config_dir)
    monkeypatch.setattr("bcli.config._defaults.CONFIG_DIR", config_dir)
    monkeypatch.setattr("bcli.config._defaults.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._defaults.REGISTRIES_DIR", registries_dir)
    monkeypatch.setattr("bcli.registry._registry.REGISTRIES_DIR", registries_dir)
    monkeypatch.setattr("bcli.config._loader._find_project_config", lambda: None)
    for env_var in ("BCLI_PROFILE", "BCLI_FORMAT", "BCLI_TIMEOUT"):
        monkeypatch.delenv(env_var, raising=False)

    # Reset state singleton (lazy-loaded config/registry).
    state._config = None
    state._registry = None
    state._telemetry = None
    state.profile_name = None
    state.env_override = None
    state.company_override = None
    state.format_explicit = False
    state.format = "table"
    yield {
        "config_dir": config_dir,
        "config_file": config_file,
        "registries_dir": registries_dir,
        "describe_dir": describe_dir,
    }
    state._config = None
    state._registry = None
    state._telemetry = None
    state.profile_name = None
    state.env_override = None
    state.company_override = None


def _write_basic_profile(config_file: Path, *, disable_writes: bool = False,
                          disable_standard_api: bool = False,
                          allowed_categories: list[str] | None = None) -> None:
    lines = [
        '[defaults]\nprofile = "test"\n',
        '[profiles.test]\n',
        'tenant_id = "t1"\n',
        'environment = "Sandbox"\n',
    ]
    if disable_writes:
        lines.append("disable_writes = true\n")
    if disable_standard_api:
        lines.append("disable_standard_api = true\n")
    if allowed_categories is not None:
        cats = ", ".join(f'"{c}"' for c in allowed_categories)
        lines.append(f"allowed_categories = [{cats}]\n")
    config_file.write_text("".join(lines))


def _write_custom_registry(registries_dir: Path, profile: str = "test") -> Path:
    """Drop a tiny custom registry with two endpoints for projection tests."""
    registry_file = registries_dir / f"{profile}.json"
    payload = {
        "endpoints": [
            {
                "entity_set_name": "vendors",
                "entity_name": "Vendor",
                "description": "Vendor master records",
                "supports": ["GET", "POST", "PATCH"],
                "key_field": "id",
                "category": "finance",
                "api_publisher": "beautech",
                "api_group": "finance",
                "api_version": "v1.0",
                "domain": "finance",
                "field_names": ["id", "number", "name"],
            },
            {
                "entity_set_name": "engineLogbook",
                "entity_name": "EngineLogbook",
                "description": "Aviation engine logbook",
                "supports": ["GET"],
                "key_field": "id",
                "category": "aviation",
                "api_publisher": "beautech",
                "api_group": "aviation",
                "api_version": "v1.0",
                "domain": "technical",
                "field_names": [],
            },
        ]
    }
    registry_file.write_text(json.dumps(payload))
    return registry_file


def _invoke_describe(*extra_args: str):
    """Run `bcli describe ...` and decode JSON from stdout when applicable."""
    return runner.invoke(app, ["describe", *extra_args])


# ─── Schema-level tests ──────────────────────────────────────────────


def test_describe_json_top_level_keys(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("--format", "json")
    assert result.exit_code == 0, result.stderr or result.stdout
    data = json.loads(result.stdout)
    for key in ("version", "tool", "tool_version", "profile",
                 "commands", "registry", "profile_constraints"):
        assert key in data, f"missing top-level key: {key!r}"
    assert data["version"] == "0.1"
    assert data["tool"] == "bcli"
    assert data["tool_version"] == __version__
    assert data["profile"] == "test"


def test_describe_commands_list_shape(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("--format", "json")
    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data["commands"], list) and data["commands"]
    for cmd in data["commands"]:
        assert "path" in cmd and isinstance(cmd["path"], list)
        assert all(isinstance(p, str) for p in cmd["path"])
        assert "summary" in cmd
        assert "options" in cmd and isinstance(cmd["options"], list)
        for opt in cmd["options"]:
            assert "name" in opt and isinstance(opt["name"], str)
            assert "type" in opt
        assert "effects" in cmd and isinstance(cmd["effects"], list)
        assert cmd["effects"], "every command must declare at least one effect"
        for e in cmd["effects"]:
            assert e in ("read", "mutating", "other"), f"unknown effect: {e!r}"
        assert "supported_formats" in cmd
        assert isinstance(cmd["supported_formats"], list)


def test_describe_lists_describe_command_itself(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("--format", "json")
    data = json.loads(result.stdout)
    paths = [tuple(c["path"]) for c in data["commands"]]
    assert ("describe",) in paths, f"describe missing from commands; got {paths!r}"
    describe_cmd = next(c for c in data["commands"] if c["path"] == ["describe"])
    assert describe_cmd["effects"] == ["read"]


def test_describe_commands_sorted_deterministically(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("--format", "json")
    data = json.loads(result.stdout)
    paths = [tuple(c["path"]) for c in data["commands"]]
    assert paths == sorted(paths), "commands must be sorted alphabetically by path"


def test_describe_mutating_commands_flagged(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("--format", "json")
    data = json.loads(result.stdout)
    cmds_by_path = {tuple(c["path"]): c for c in data["commands"]}

    mutating_paths = [
        ("post",),
        ("patch",),
        ("delete",),
        ("attach", "upload"),
        ("batch", "run"),
    ]
    for p in mutating_paths:
        assert p in cmds_by_path, f"missing mutating command: {p}"
        cmd = cmds_by_path[p]
        assert cmd["effects"] == ["mutating"], f"{p} not flagged mutating: {cmd['effects']}"
        # Forward-compat declaration for Phase 2.
        assert cmd.get("emits_result_envelope") is True, (
            f"{p} must declare emits_result_envelope=True for AIP forward-compat"
        )
        assert cmd.get("requires_confirmation") == "production", (
            f"{p} must declare requires_confirmation='production'"
        )


def test_describe_read_commands_do_not_carry_envelope_flag(tmp_config):
    """Forward-compat flags must NOT leak onto read commands."""
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("--format", "json")
    data = json.loads(result.stdout)
    cmds_by_path = {tuple(c["path"]): c for c in data["commands"]}
    get_cmd = cmds_by_path[("get",)]
    assert get_cmd["effects"] == ["read"]
    assert "emits_result_envelope" not in get_cmd
    assert "emits_operation_state" not in get_cmd
    assert "requires_confirmation" not in get_cmd


def test_describe_batch_run_emits_operation_state(tmp_config):
    """Only `batch run` is multi-step durable — declares operation_state for Phase 3."""
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("--format", "json")
    data = json.loads(result.stdout)
    cmds_by_path = {tuple(c["path"]): c for c in data["commands"]}

    batch_run = cmds_by_path[("batch", "run")]
    assert batch_run.get("emits_operation_state") is True, (
        "batch run must declare emits_operation_state=True"
    )

    # And single-mutation commands do NOT get the operation_state flag.
    for p in [("post",), ("patch",), ("delete",), ("attach", "upload")]:
        assert "emits_operation_state" not in cmds_by_path[p], (
            f"{p} must not declare emits_operation_state — that's batch-only"
        )


# ─── Registry projection ─────────────────────────────────────────────


def test_describe_registry_projection_with_custom_endpoints(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    _write_custom_registry(tmp_config["registries_dir"], profile="test")
    result = _invoke_describe("--format", "json")
    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    reg = data["registry"]
    assert reg["tier_1_custom_count"] == 2
    assert reg["tier_2_standard_enabled"] is True
    endpoints = reg["endpoints"]
    assert isinstance(endpoints, list) and len(endpoints) >= 2
    # Sorted alphabetically by entity for determinism.
    custom_entities = [e["entity"] for e in endpoints if e["tier"] == "custom"]
    assert custom_entities == sorted(custom_entities)

    vendors = next(e for e in endpoints if e["entity"] == "vendors")
    assert vendors["tier"] == "custom"
    assert vendors["domain"] == "finance"
    assert vendors["ops"] == ["GET", "POST", "PATCH"]
    assert vendors["fields_known"] == 3
    assert "fields_discovered_at" in vendors  # may be null


def test_describe_registry_reflects_disable_standard_api(tmp_config):
    _write_basic_profile(tmp_config["config_file"], disable_standard_api=True)
    _write_custom_registry(tmp_config["registries_dir"], profile="test")
    result = _invoke_describe("--format", "json")
    data = json.loads(result.stdout)
    assert data["registry"]["tier_2_standard_enabled"] is False


# ─── Profile constraints ─────────────────────────────────────────────


def test_describe_profile_constraints_projection(tmp_config):
    _write_basic_profile(
        tmp_config["config_file"],
        disable_writes=True,
        disable_standard_api=True,
        allowed_categories=["finance", "aviation"],
    )
    result = _invoke_describe("--format", "json")
    data = json.loads(result.stdout)
    constraints = data["profile_constraints"]
    assert constraints["disable_writes"] is True
    assert constraints["disable_standard_api"] is True
    assert constraints["allowed_categories"] == ["finance", "aviation"]


def test_describe_profile_constraints_defaults_to_unset(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("--format", "json")
    data = json.loads(result.stdout)
    constraints = data["profile_constraints"]
    # disable_writes / disable_standard_api default to False on the model.
    assert constraints["disable_writes"] is False
    assert constraints["disable_standard_api"] is False
    # No allowed_categories set on the profile → null in the projection.
    assert constraints["allowed_categories"] is None or constraints["allowed_categories"] == []


# ─── No-profile / broken-config tolerance ────────────────────────────


def test_describe_tolerates_no_profile_configured(tmp_config):
    """Agent-first: describe must work even when no profile is set up.

    `doctor` tolerates a broken install; describe is the *first* command
    an agent will run — it has to produce output even when the user
    hasn't run `bcli config init` yet.
    """
    # No config file written.
    result = _invoke_describe("--format", "json")
    assert result.exit_code == 0, result.stderr or result.stdout
    data = json.loads(result.stdout)
    assert data["profile"] is None
    # commands list still populated from the live Typer app.
    assert data["commands"]
    # Registry projection should not crash.
    assert "registry" in data


# ─── Subtree mode ────────────────────────────────────────────────────


def test_describe_subtree_returns_only_matching_command(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    full = json.loads(_invoke_describe("--format", "json").stdout)
    sub = json.loads(_invoke_describe("get", "--format", "json").stdout)
    # Subtree must keep provenance keys.
    assert sub["version"] == "0.1"
    assert sub["tool"] == "bcli"
    assert sub["tool_version"] == full["tool_version"]
    assert sub["profile"] == "test"
    # Subtree carries exactly one command — the `get`.
    assert isinstance(sub["commands"], list)
    assert len(sub["commands"]) == 1
    assert sub["commands"][0]["path"] == ["get"]
    # Drops registry + profile_constraints to save tokens.
    assert "registry" not in sub
    assert "profile_constraints" not in sub
    # Smaller payload — fundamental purpose of the subtree mode.
    assert len(json.dumps(sub)) < len(json.dumps(full))


def test_describe_subtree_supports_multipart_path(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    sub = json.loads(_invoke_describe("batch", "run", "--format", "json").stdout)
    assert len(sub["commands"]) == 1
    assert sub["commands"][0]["path"] == ["batch", "run"]
    assert sub["commands"][0]["effects"] == ["mutating"]


def test_describe_subtree_unknown_path_errors(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("not-a-real-command", "--format", "json")
    assert result.exit_code != 0


# ─── Cache behavior ───────────────────────────────────────────────────


def test_describe_writes_cache_on_first_call(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    describe_dir = tmp_config["describe_dir"]
    assert not describe_dir.exists()
    result = _invoke_describe("--format", "json")
    assert result.exit_code == 0, result.stderr or result.stdout
    # Cache directory created.
    assert describe_dir.exists()
    cached_files = list(describe_dir.glob("test.*.json"))
    assert len(cached_files) == 1, f"expected 1 cache file, got {cached_files}"


def test_describe_cache_hit_returns_cached_payload(tmp_config):
    """Second call reads from cache rather than regenerating.

    Verified by writing a sentinel into the cache file and seeing it
    return verbatim on the second call — bypassing fresh generation.
    """
    _write_basic_profile(tmp_config["config_file"])
    first = _invoke_describe("--format", "json")
    assert first.exit_code == 0

    # Mutate the cache file to a known sentinel value; second call must
    # return that sentinel (proving cache read path).
    describe_dir = tmp_config["describe_dir"]
    cached_files = list(describe_dir.glob("test.*.json"))
    assert len(cached_files) == 1
    sentinel = {
        "version": "0.1",
        "tool": "bcli",
        "tool_version": "sentinel-cache-version",
        "profile": "test",
        "commands": [],
        "registry": {"tier_1_custom_count": 0, "tier_2_standard_enabled": True, "endpoints": []},
        "profile_constraints": {"disable_writes": False, "disable_standard_api": False,
                                  "allowed_categories": None},
    }
    cached_files[0].write_text(json.dumps(sentinel))

    second = _invoke_describe("--format", "json")
    assert second.exit_code == 0
    data = json.loads(second.stdout)
    assert data["tool_version"] == "sentinel-cache-version", "cache was not consulted"


def test_describe_cache_invalidates_on_registry_mtime(tmp_config):
    """Touching the registry file forces a regenerate on the next call."""
    _write_basic_profile(tmp_config["config_file"])
    registry_file = _write_custom_registry(tmp_config["registries_dir"], profile="test")

    first = _invoke_describe("--format", "json")
    assert first.exit_code == 0
    describe_dir = tmp_config["describe_dir"]
    cached_files = list(describe_dir.glob("test.*.json"))
    assert len(cached_files) == 1
    first_hash_name = cached_files[0].name

    # Sleep just enough for filesystem mtime granularity (1s on most
    # filesystems is plenty); then touch the registry file so its mtime
    # advances, forcing cache invalidation.
    time.sleep(1.1)
    registry_file.write_text(registry_file.read_text())  # rewrite => new mtime

    second = _invoke_describe("--format", "json")
    assert second.exit_code == 0
    cached_files_after = list(describe_dir.glob("test.*.json"))
    new_names = {f.name for f in cached_files_after}
    # Either a brand-new cache file appeared (different hash, ideal case)
    # OR the existing file's mtime advanced — both prove regeneration.
    appeared_new = bool(new_names - {first_hash_name})
    same_file_advanced = any(
        f.stat().st_mtime > cached_files[0].stat().st_mtime
        for f in cached_files_after if f.name == first_hash_name
    )
    assert appeared_new or same_file_advanced, (
        "cache must regenerate when registry mtime advances; "
        f"before={first_hash_name} after={new_names}"
    )


# ─── Exit codes projection (Phase 4a) ────────────────────────────────


def test_describe_includes_exit_codes(tmp_config):
    """Phase 4a: top-level `exit_codes` projects the CLI taxonomy.

    Agents consume `bcli describe` to learn how to interpret a non-zero
    `bcli` exit. The map MUST include every documented code so the
    runtime can render meaningful errors.
    """
    _write_basic_profile(tmp_config["config_file"])
    result = _invoke_describe("--format", "json")
    assert result.exit_code == 0, result.stderr or result.stdout
    data = json.loads(result.stdout)
    assert "exit_codes" in data, "top-level exit_codes key missing"
    codes = data["exit_codes"]
    keyset = {int(k) for k in codes.keys()}
    for expected in (0, 1, 2, 3, 4, 5, 6, 7, 8):
        assert expected in keyset, f"missing exit code {expected} in describe output"
    for label in codes.values():
        assert isinstance(label, str) and label


def test_describe_subtree_drops_exit_codes(tmp_config):
    """Subtree mode trims metadata — exit_codes belongs to the full doc."""
    _write_basic_profile(tmp_config["config_file"])
    sub = json.loads(_invoke_describe("get", "--format", "json").stdout)
    assert "exit_codes" not in sub


# ─── Table format smoke test ─────────────────────────────────────────


def test_describe_table_format_smoke(tmp_config):
    _write_basic_profile(tmp_config["config_file"])
    result = runner.invoke(app, ["describe", "--format", "table"])
    assert result.exit_code == 0, result.stderr
    # Some human-readable rendering of the data; "get" command should appear.
    assert "get" in result.stdout
