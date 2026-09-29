"""Tests for hand-written registry files, merge-on-import, and export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli.registry._importers import (
    export_custom_registry,
    import_from_file,
    save_custom_registry,
)
from bcli.registry._registry import EndpointRegistry
from bcli.registry._schema import EndpointMetadata
from bcli_cli._state import state
from bcli_cli.app import app

runner = CliRunner()

MINIMAL_YAML = """\
publisher: contoso
group: integration
version: v1.0
endpoints:
  - shipmentTrackings
  - entity_set_name: carrierRates
    supports: [GET, POST]
    description: Carrier rate cards
"""


@pytest.fixture
def registries_dir(tmp_path, monkeypatch):
    d = tmp_path / "registries"
    monkeypatch.setattr("bcli.registry._importers.REGISTRIES_DIR", d)
    monkeypatch.setattr("bcli.registry._registry.REGISTRIES_DIR", d)
    return d


@pytest.fixture
def cli_state(registries_dir):
    state._config = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={"dev": BCProfile(tenant_id="t", environment="Sandbox")},
    )
    state._registry = None
    state.profile_name = None
    yield registries_dir
    state._config = None
    state._registry = None
    state.profile_name = None


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestImportFromFile:
    def test_top_level_route_is_inherited_by_bare_names(self, tmp_path):
        endpoints = import_from_file(_write(tmp_path, "apis.yaml", MINIMAL_YAML))

        by_name = {e.entity_set_name: e for e in endpoints}
        assert set(by_name) == {"shipmentTrackings", "carrierRates"}
        tracking = by_name["shipmentTrackings"]
        assert tracking.route_display == "contoso/integration/v1.0"
        assert tracking.entity_name == "shipmentTracking"
        assert tracking.key_field == "systemId"
        assert tracking.category == "integration"
        assert by_name["carrierRates"].supports == ["GET", "POST"]

    def test_entry_route_overrides_file_defaults(self, tmp_path):
        text = json.dumps({
            "publisher": "contoso",
            "group": "integration",
            "version": "v1.0",
            "endpoints": [{"entity_set_name": "budgets", "group": "finance", "version": "v2.0"}],
        })
        [ep] = import_from_file(_write(tmp_path, "apis.json", text))
        assert ep.route_display == "contoso/finance/v2.0"

    def test_partial_route_is_rejected(self, tmp_path):
        text = "publisher: contoso\nendpoints:\n  - shipmentTrackings\n"
        with pytest.raises(ValueError, match="shipmentTrackings"):
            import_from_file(_write(tmp_path, "apis.yml", text))

    def test_utf8_bom_is_accepted(self, tmp_path):
        path = tmp_path / "apis.json"
        path.write_bytes(
            b"\xef\xbb\xbf"
            + json.dumps({"publisher": "c", "group": "g", "version": "v1.0",
                          "endpoints": ["things"]}).encode()
        )
        assert [e.entity_set_name for e in import_from_file(path)] == ["things"]


class TestShortRouteKeys:
    def test_metadata_accepts_short_keys(self):
        ep = EndpointMetadata.model_validate(
            {"entity_set_name": "things", "publisher": "c", "group": "g", "version": "v1.0"}
        )
        assert ep.is_custom
        assert ep.model_dump()["api_publisher"] == "c"

    def test_registry_file_with_short_keys_routes_as_custom(self, registries_dir):
        registries_dir.mkdir()
        (registries_dir / "dev.json").write_text(json.dumps({"endpoints": [
            {"entity_set_name": "things", "publisher": "c", "group": "g", "version": "v1.0"},
        ]}))
        meta = EndpointRegistry(profile_name="dev").resolve("things")
        assert meta.route_display == "c/g/v1.0"


class TestSaveMerges:
    def _custom(self, name: str, group: str) -> EndpointMetadata:
        return EndpointMetadata(
            entity_set_name=name, api_publisher="contoso", api_group=group, api_version="v1.0",
        )

    def _names(self, path: Path) -> set[str]:
        return {e["entity_set_name"] for e in json.loads(path.read_text())["endpoints"]}

    def test_second_import_keeps_first(self, registries_dir):
        save_custom_registry("dev", [self._custom("shipmentTrackings", "integration")])
        path = save_custom_registry("dev", [self._custom("budgets", "finance")])
        assert self._names(path) == {"shipmentTrackings", "budgets"}
        assert json.loads(path.read_text())["endpoint_count"] == 2

    def test_reimport_updates_in_place(self, registries_dir):
        save_custom_registry("dev", [self._custom("budgets", "finance")])
        path = save_custom_registry("dev", [self._custom("Budgets", "planning")])
        [entry] = json.loads(path.read_text())["endpoints"]
        assert entry["api_group"] == "planning"

    def test_replace_drops_imports_but_keeps_pack_entries(self, registries_dir):
        registries_dir.mkdir()
        (registries_dir / "dev.json").write_text(json.dumps({"endpoints": [
            {"entity_set_name": "oldImport", "api_publisher": "c", "api_group": "g", "api_version": "v1.0"},
            {"entity_set_name": "fromPack", "api_publisher": "c", "api_group": "g",
             "api_version": "v1.0", "source_pack": "starter"},
        ]}))
        path = save_custom_registry(
            "dev", [self._custom("budgets", "finance")], replace=True,
        )
        assert self._names(path) == {"fromPack", "budgets"}


class TestExport:
    def test_export_round_trips_through_import(self, registries_dir, tmp_path):
        imported = import_from_file(_write(tmp_path, "apis.yaml", MINIMAL_YAML))
        save_custom_registry("dev", imported)

        exported = export_custom_registry("dev")
        out = _write(tmp_path, "export.json", json.dumps(exported))
        again = import_from_file(out)

        assert {e.entity_set_name for e in again} == {"shipmentTrackings", "carrierRates"}
        assert all(e.route_display == "contoso/integration/v1.0" for e in again)

    def test_export_strips_pack_provenance(self, registries_dir):
        registries_dir.mkdir()
        (registries_dir / "dev.json").write_text(json.dumps({"endpoints": [
            {"entity_set_name": "fromPack", "api_publisher": "c", "api_group": "g",
             "api_version": "v1.0", "source_pack": "starter", "pack_version": "1.0.0"},
        ]}))
        [entry] = export_custom_registry("dev")["endpoints"]
        assert "source_pack" not in entry
        assert "pack_version" not in entry

    def test_export_of_missing_profile_is_empty(self, registries_dir):
        assert export_custom_registry("nobody") == {"endpoints": []}


class TestRegistryCli:
    def test_import_from_yaml_file(self, cli_state, tmp_path):
        path = _write(tmp_path, "apis.yaml", MINIMAL_YAML)
        result = runner.invoke(app, ["registry", "import", "--from-file", str(path)])
        assert result.exit_code == 0, result.output
        assert "contoso/integration/v1.0: 2" in result.output
        assert (cli_state / "dev.json").is_file()

    def test_import_invalid_file_exits_validation(self, cli_state, tmp_path):
        path = _write(tmp_path, "apis.yaml", "publisher: contoso\nendpoints: [things]\n")
        result = runner.invoke(app, ["registry", "import", "--from-file", str(path)])
        assert result.exit_code == 5

    def test_from_metadata_without_route_is_usage_error(self, cli_state):
        result = runner.invoke(app, ["registry", "import", "--from-metadata"])
        assert result.exit_code == 2
        assert "--publisher" in result.output

    def test_export_to_stdout(self, cli_state, tmp_path):
        path = _write(tmp_path, "apis.yaml", MINIMAL_YAML)
        runner.invoke(app, ["registry", "import", "--from-file", str(path)])
        result = runner.invoke(app, ["registry", "export"])
        assert result.exit_code == 0, result.output
        names = {e["entity_set_name"] for e in json.loads(result.output)["endpoints"]}
        assert names == {"shipmentTrackings", "carrierRates"}

    def test_legacy_from_json_flag_still_works(self, cli_state, tmp_path):
        path = _write(tmp_path, "apis.json", json.dumps({
            "endpoints": [{"entity_set_name": "things", "api_publisher": "c",
                           "api_group": "g", "api_version": "v1.0"}],
        }))
        result = runner.invoke(app, ["registry", "import", "--from-json", str(path)])
        assert result.exit_code == 0, result.output
