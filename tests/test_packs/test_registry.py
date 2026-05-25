"""Pack discovery — built-in scan + entry-point group + name overlay."""

from __future__ import annotations

import logging
from pathlib import Path


from bcli.packs import Pack, discover_all, discover_builtin_packs


def test_discover_finds_builtin_packs() -> None:
    """The OSS repo ships starter-generic + cronus-demo under packs/."""
    packs = discover_builtin_packs()
    names = {p.name for p in packs}
    assert "starter-generic" in names
    assert "cronus-demo" in names


def test_discover_all_includes_builtins() -> None:
    packs = discover_all()
    assert "starter-generic" in packs
    assert isinstance(packs["starter-generic"], Pack)


def test_discover_skips_dirs_without_pack_yaml(
    tmp_path: Path, caplog
) -> None:
    """A subdir that lacks pack.yaml is silently skipped (not an error)."""
    (tmp_path / "not-a-pack").mkdir()
    (tmp_path / "not-a-pack" / "README.md").write_text("hi")
    packs = discover_builtin_packs(root=tmp_path)
    assert packs == []


def test_discover_logs_warning_on_broken_pack(
    tmp_path: Path, caplog
) -> None:
    """A directory with a broken pack.yaml logs a warning but does not crash."""
    pack_dir = tmp_path / "broken"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text("not: [valid", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="bcli.packs"):
        packs = discover_builtin_packs(root=tmp_path)
    assert packs == []
    assert any("Skipping built-in pack" in rec.message for rec in caplog.records)
