"""Manifest schema validation + content loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from bcli.packs import PackLoadError, load_pack
from bcli.packs._protocol import TARGET_AGENTS, TARGET_CLAUDE


def test_load_basic_pack(make_pack) -> None:
    src = make_pack(
        "demo",
        fragments={"a.md": "Hello", "b.md": "World"},
        queries={"vendors-q": {"endpoint": "vendors", "top": 1}},
        batches={"weekly.yaml": "name: weekly\n"},
        recommended_context_providers=["x"],
    )
    pack = load_pack(src)
    assert pack.name == "demo"
    assert pack.version == "0.1.0"
    assert len(pack.contents.agent_fragments) == 2
    assert pack.contents.agent_fragments[0].targets == (TARGET_AGENTS,)
    assert len(pack.contents.queries) == 1
    assert pack.contents.queries[0].name == "vendors-q"
    assert pack.contents.queries[0].body["endpoint"] == "vendors"
    assert len(pack.contents.batches) == 1
    assert pack.contents.batches[0].body == "name: weekly\n"
    assert pack.manifest.recommended_context_providers == ("x",)


def test_load_pack_respects_fragment_targets(make_pack) -> None:
    src = make_pack(
        "tgts",
        fragments={"common.md": "common", "claude-only.md": "claude only"},
        fragment_targets={
            "common.md": ["agents", "claude"],
            "claude-only.md": ["claude"],
        },
    )
    pack = load_pack(src)
    by_name = {f.name: f for f in pack.contents.agent_fragments}
    assert by_name["common.md"].targets == (TARGET_AGENTS, TARGET_CLAUDE)
    assert by_name["claude-only.md"].targets == (TARGET_CLAUDE,)


def test_load_pack_invalid_target_rejected(tmp_path) -> None:
    """A fragment declaring an unknown target must be rejected."""
    src = tmp_path / "bad"
    (src / "fragments").mkdir(parents=True)
    (src / "fragments" / "a.md").write_text("body")
    # Author the manifest with valid YAML directly — the loader's
    # target validator should still fire on "evil".
    (src / "pack.yaml").write_text(
        "name: bad\n"
        "version: 0.1.0\n"
        "contents:\n"
        "  agent_fragments:\n"
        "    - name: a.md\n"
        "      targets: [evil]\n",
        encoding="utf-8",
    )
    with pytest.raises(PackLoadError, match="invalid targets"):
        load_pack(src)


def test_load_pack_missing_required_field(tmp_path: Path) -> None:
    src = tmp_path / "broken"
    src.mkdir()
    (src / "pack.yaml").write_text("description: nope\n")
    with pytest.raises(PackLoadError, match="missing required field 'name'"):
        load_pack(src)


def test_load_pack_missing_fragment_file(tmp_path: Path) -> None:
    src = tmp_path / "missing-frag"
    src.mkdir()
    (src / "pack.yaml").write_text(
        "name: x\nversion: 0.1.0\ncontents:\n  agent_fragments: [absent.md]\n",
        encoding="utf-8",
    )
    with pytest.raises(PackLoadError, match="not found"):
        load_pack(src)


def test_load_pack_invalid_yaml(tmp_path: Path) -> None:
    src = tmp_path / "yaml-broken"
    src.mkdir()
    (src / "pack.yaml").write_text("not: [valid", encoding="utf-8")
    with pytest.raises(PackLoadError, match="not valid YAML"):
        load_pack(src)
