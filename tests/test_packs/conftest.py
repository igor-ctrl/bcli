"""Shared fixtures for pack tests — build small packs on the fly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def make_pack(tmp_path: Path):
    """Factory: ``make_pack(name, **content)`` returns the pack dir."""

    def _make(
        name: str = "demo",
        version: str = "0.1.0",
        *,
        fragments: dict[str, str] | None = None,
        fragment_targets: dict[str, list[str]] | None = None,
        queries: dict[str, dict] | None = None,
        batches: dict[str, str] | None = None,
        presets: dict[str, dict] | None = None,
        recommended_context_providers: list[str] | None = None,
    ) -> Path:
        root = tmp_path / "packs-src" / name
        root.mkdir(parents=True, exist_ok=True)
        manifest: dict = {
            "name": name,
            "version": version,
            "description": f"test pack {name}",
            "contents": {},
        }
        contents = manifest["contents"]

        if fragments:
            (root / "fragments").mkdir(exist_ok=True)
            specs = []
            for fname, body in fragments.items():
                (root / "fragments" / fname).write_text(body, encoding="utf-8")
                if fragment_targets and fname in fragment_targets:
                    specs.append({"name": fname, "targets": fragment_targets[fname]})
                else:
                    specs.append(fname)
            contents["agent_fragments"] = specs

        if queries:
            (root / "queries").mkdir(exist_ok=True)
            query_files = []
            for qname, body in queries.items():
                file = f"{qname}.yaml"
                (root / "queries" / file).write_text(
                    yaml.safe_dump({"queries": {qname: body}}, sort_keys=False),
                    encoding="utf-8",
                )
                query_files.append(file)
            contents["queries"] = query_files

        if batches:
            (root / "batches").mkdir(exist_ok=True)
            batch_files = []
            for filename, body in batches.items():
                (root / "batches" / filename).write_text(body, encoding="utf-8")
                batch_files.append(filename)
            contents["batches"] = batch_files

        if presets:
            (root / "presets").mkdir(exist_ok=True)
            preset_files = []
            for pname, body in presets.items():
                file = f"{pname}.json"
                (root / "presets" / file).write_text(
                    json.dumps({"endpoints": {pname: body}}),
                    encoding="utf-8",
                )
                preset_files.append(file)
            contents["registry_presets"] = preset_files

        if recommended_context_providers:
            manifest["recommended_context_providers"] = list(
                recommended_context_providers
            )

        (root / "pack.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        return root

    return _make


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Per-test ``~/.config/bcli`` substitute."""
    p = tmp_path / "config"
    p.mkdir()
    return p


@pytest.fixture
def install_target(tmp_path: Path) -> Path:
    """Per-test fake project root for the install target."""
    p = tmp_path / "target"
    p.mkdir()
    return p
