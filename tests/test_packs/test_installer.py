"""Install / uninstall round-trips, idempotency, conflict detection."""

from __future__ import annotations

import json

import pytest
import yaml

from bcli.packs import (
    InstallError,
    install_pack,
    load_pack,
    read_ledger,
    uninstall_pack,
)
from bcli.packs._installer import (
    batches_dir,
    fragments_dir,
    queries_path,
    registries_path,
)


def test_install_writes_all_artefacts(make_pack, config_dir, install_target) -> None:
    src = make_pack(
        "demo",
        fragments={"common-errors.md": "## Common errors\nfoo"},
        queries={"vendor-by-no": {"endpoint": "vendors", "top": 1}},
        batches={"weekly.yaml": "name: weekly\n"},
        presets={"myEntity": {"entity_set_name": "myEntity", "supports": ["GET"]}},
    )
    pack = load_pack(src)
    install_pack(
        pack,
        profile="prod",
        target=install_target,
        dry_run=False,
        config_override=config_dir,
    )

    # Fragment file lands at .claude/agent.d/bcli-demo/.
    frag_path = fragments_dir(install_target, "demo") / "common-errors.md"
    assert frag_path.is_file()
    assert "Common errors" in frag_path.read_text()

    # Marker block spliced into AGENTS.md (default target).
    agents = (install_target / "AGENTS.md").read_text()
    assert "bcli-pack:demo:common-errors.md START" in agents
    assert "Common errors" in agents
    assert "content_hash: sha256:" in agents

    # Queries merged into config_dir/queries/prod.yaml.
    qpath = queries_path("prod", override=config_dir)
    assert qpath.is_file()
    raw = yaml.safe_load(qpath.read_text())
    assert "vendor-by-no" in raw["queries"]
    assert raw["queries"]["vendor-by-no"]["provenance"]["source_pack"] == "demo"

    # Batch file written.
    batch_path = batches_dir("prod", override=config_dir) / "weekly.yaml"
    assert batch_path.is_file()

    # Registry preset merged with provenance.
    rpath = registries_path("prod", override=config_dir)
    assert rpath.is_file()
    reg = json.loads(rpath.read_text())
    assert "myEntity" in reg["endpoints"]
    assert reg["endpoints"]["myEntity"]["source_pack"] == "demo"
    assert reg["endpoints"]["myEntity"]["pack_version"] == "0.1.0"

    # Ledger persisted.
    ledger = read_ledger("demo", "prod", config_dir=config_dir)
    assert ledger is not None
    assert ledger.pack_name == "demo"
    assert len(ledger.paths) >= 4  # fragment file + block + query + batch + preset
    assert any(p.kind == "agents_block" for p in ledger.paths)


def test_fragment_targets_route_blocks(make_pack, config_dir, install_target) -> None:
    src = make_pack(
        "tgts",
        fragments={
            "agents-only.md": "AG",
            "claude-only.md": "CL",
            "both.md": "BOTH",
        },
        fragment_targets={
            "agents-only.md": ["agents"],
            "claude-only.md": ["claude"],
            "both.md": ["agents", "claude"],
        },
    )
    pack = load_pack(src)
    install_pack(
        pack,
        profile="prod",
        target=install_target,
        dry_run=False,
        config_override=config_dir,
    )
    agents = (install_target / "AGENTS.md").read_text()
    claude = (install_target / "CLAUDE.md").read_text()

    # agents-only present in AGENTS, absent from CLAUDE.
    assert "bcli-pack:tgts:agents-only.md START" in agents
    assert "bcli-pack:tgts:agents-only.md START" not in claude

    # claude-only present in CLAUDE, absent from AGENTS.
    assert "bcli-pack:tgts:claude-only.md START" in claude
    assert "bcli-pack:tgts:claude-only.md START" not in agents

    # both present in both.
    assert "bcli-pack:tgts:both.md START" in agents
    assert "bcli-pack:tgts:both.md START" in claude


def test_idempotent_reinstall_no_diff(make_pack, config_dir, install_target) -> None:
    src = make_pack("demo", fragments={"a.md": "AAA"}, queries={"q": {"endpoint": "vendors"}})
    pack = load_pack(src)
    install_pack(
        pack, profile="prod", target=install_target, dry_run=False,
        config_override=config_dir,
    )
    agents_first = (install_target / "AGENTS.md").read_text()
    qpath = queries_path("prod", override=config_dir)
    queries_first = qpath.read_text()

    # Second install — same content, must not duplicate the block.
    install_pack(
        pack, profile="prod", target=install_target, dry_run=False,
        config_override=config_dir,
    )
    agents_second = (install_target / "AGENTS.md").read_text()
    # Marker pair appears exactly once.
    assert agents_first.count("bcli-pack:demo:a.md START") == 1
    assert agents_second.count("bcli-pack:demo:a.md START") == 1
    # Queries file content identical (provenance also identical).
    assert qpath.read_text() == queries_first


def test_conflict_blocks_second_pack(make_pack, config_dir, install_target) -> None:
    a = load_pack(make_pack(
        "alpha",
        presets={"shared": {"entity_set_name": "shared", "supports": ["GET"]}},
    ))
    b = load_pack(make_pack(
        "beta",
        presets={"shared": {"entity_set_name": "shared", "supports": ["GET"]}},
    ))
    install_pack(
        a, profile="prod", target=install_target, dry_run=False,
        config_override=config_dir,
    )
    # Second pack on same endpoint must refuse.
    with pytest.raises(InstallError, match="conflict"):
        install_pack(
            b, profile="prod", target=install_target, dry_run=False,
            config_override=config_dir,
        )
    # With explicit two-flag override, the install succeeds.
    install_pack(
        b, profile="prod", target=install_target, dry_run=False,
        replace_owned=True, accept_conflicts=True,
        config_override=config_dir,
    )
    reg = json.loads(registries_path("prod", override=config_dir).read_text())
    assert reg["endpoints"]["shared"]["source_pack"] == "beta"


def test_uninstall_removes_artefacts(make_pack, config_dir, install_target) -> None:
    src = make_pack(
        "demo",
        fragments={"a.md": "A"},
        queries={"q-x": {"endpoint": "vendors"}},
        batches={"b.yaml": "name: b\n"},
        presets={"epX": {"entity_set_name": "epX", "supports": ["GET"]}},
    )
    pack = load_pack(src)
    install_pack(
        pack, profile="prod", target=install_target, dry_run=False,
        config_override=config_dir,
    )
    # Pre-condition.
    frag_path = fragments_dir(install_target, "demo") / "a.md"
    assert frag_path.is_file()

    result = uninstall_pack("demo", profile="prod", config_override=config_dir)

    # All files removed.
    assert not frag_path.is_file()
    batch_path = batches_dir("prod", override=config_dir) / "b.yaml"
    assert not batch_path.is_file()
    # Query gone from merged YAML.
    raw = yaml.safe_load(queries_path("prod", override=config_dir).read_text())
    assert "q-x" not in (raw.get("queries") or {})
    # Marker stripped.
    agents = (install_target / "AGENTS.md").read_text()
    assert "bcli-pack:demo:a.md START" not in agents
    # Preset removed.
    reg = json.loads(registries_path("prod", override=config_dir).read_text())
    assert "epX" not in (reg.get("endpoints") or {})
    # Ledger gone.
    assert read_ledger("demo", "prod", config_dir=config_dir) is None
    # No catastrophic warnings on a clean install/uninstall.
    assert all("missing" not in w for w in result.warnings)


def test_dry_run_writes_nothing(make_pack, config_dir, install_target) -> None:
    pack = load_pack(make_pack("demo", fragments={"a.md": "A"}))
    plan = install_pack(
        pack, profile="prod", target=install_target, dry_run=True,
        config_override=config_dir,
    )
    # Plan populated.
    assert plan.fragment_writes
    # Nothing on disk.
    assert not (install_target / "AGENTS.md").exists()
    assert not (config_dir / "queries").exists()
