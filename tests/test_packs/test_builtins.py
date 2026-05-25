"""End-to-end install of the two in-repo OSS packs."""

from __future__ import annotations

from pathlib import Path

import yaml

from bcli.packs import (
    discover_builtin_packs,
    install_pack,
    read_ledger,
    uninstall_pack,
)
from bcli.packs._installer import (
    batches_dir,
    fragments_dir,
    queries_path,
)


def _get(name: str):
    for p in discover_builtin_packs():
        if p.name == name:
            return p
    raise AssertionError(f"built-in pack {name!r} not found")


def test_starter_generic_installs_cleanly(
    tmp_path: Path, config_dir, install_target
) -> None:
    pack = _get("starter-generic")
    install_pack(
        pack,
        profile="prod",
        target=install_target,
        dry_run=False,
        config_override=config_dir,
    )
    led = read_ledger("starter-generic", "prod", config_dir=config_dir)
    assert led is not None

    # 6 queries land.
    qpath = queries_path("prod", override=config_dir)
    raw = yaml.safe_load(qpath.read_text())
    assert set(raw["queries"].keys()) == {
        "vendor-by-no", "customer-by-no", "open-pos", "ar-aging-buckets",
        "recent-posted-invoices", "inventory-on-hand",
    }

    # 2 batches land.
    bdir = batches_dir("prod", override=config_dir)
    assert (bdir / "weekly-ar-snapshot.yaml").is_file()
    assert (bdir / "month-end-readonly-audit.yaml").is_file()

    # 3 fragment files land + 3 marker blocks in AGENTS.md.
    fdir = fragments_dir(install_target, "starter-generic")
    assert (fdir / "endpoint-discovery.md").is_file()
    assert (fdir / "filter-syntax-cheatsheet.md").is_file()
    assert (fdir / "common-errors.md").is_file()
    agents = (install_target / "AGENTS.md").read_text()
    assert "bcli-pack:starter-generic:common-errors.md START" in agents


def test_cronus_demo_installs_cleanly(
    tmp_path: Path, config_dir, install_target
) -> None:
    pack = _get("cronus-demo")
    install_pack(
        pack,
        profile="cronus",
        target=install_target,
        dry_run=False,
        config_override=config_dir,
    )
    led = read_ledger("cronus-demo", "cronus", config_dir=config_dir)
    assert led is not None
    # Demo pack ships a month-end batch + 2 fragments. Each fragment
    # produces START + END markers, so 2 fragments = 4 marker lines.
    bdir = batches_dir("cronus", override=config_dir)
    assert (bdir / "month-end-cronus.yaml").is_file()
    agents = (install_target / "AGENTS.md").read_text()
    assert agents.count("bcli-pack:cronus-demo:cronus-orientation.md START") == 1
    assert agents.count("bcli-pack:cronus-demo:month-end-walkthrough.md START") == 1


def test_both_packs_coexist(tmp_path: Path, config_dir, install_target) -> None:
    starter = _get("starter-generic")
    cronus = _get("cronus-demo")
    install_pack(
        starter, profile="prod", target=install_target, dry_run=False,
        config_override=config_dir,
    )
    install_pack(
        cronus, profile="prod", target=install_target, dry_run=False,
        config_override=config_dir,
    )
    # Both ledgers exist; both fragment dirs exist.
    assert read_ledger("starter-generic", "prod", config_dir=config_dir) is not None
    assert read_ledger("cronus-demo", "prod", config_dir=config_dir) is not None
    assert fragments_dir(install_target, "starter-generic").is_dir()
    assert fragments_dir(install_target, "cronus-demo").is_dir()
    # Uninstalling one leaves the other intact.
    uninstall_pack("starter-generic", profile="prod", config_override=config_dir)
    assert read_ledger("starter-generic", "prod", config_dir=config_dir) is None
    assert read_ledger("cronus-demo", "prod", config_dir=config_dir) is not None
