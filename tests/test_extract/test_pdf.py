"""PDF preflight tests — size/page validation without hitting the network."""

from __future__ import annotations

from pathlib import Path

import pytest

from bcli.errors import ExtractError
from bcli.extract._pdf import preflight_pdf


def _make_pdf(path: Path, *, pages: int) -> None:
    """Build a minimal multi-page PDF with pypdf — gives us a real file the
    preflight can inspect."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as f:
        writer.write(f)


def test_preflight_returns_page_count(tmp_path: Path) -> None:
    p = tmp_path / "small.pdf"
    _make_pdf(p, pages=3)
    assert preflight_pdf(p, max_bytes=10_000_000, max_pages=100) == 3


def test_preflight_rejects_oversized(tmp_path: Path) -> None:
    p = tmp_path / "any.pdf"
    _make_pdf(p, pages=1)
    with pytest.raises(ExtractError, match="MB; backend limit"):
        preflight_pdf(p, max_bytes=1, max_pages=100)


def test_preflight_rejects_too_many_pages(tmp_path: Path) -> None:
    p = tmp_path / "many.pdf"
    _make_pdf(p, pages=5)
    with pytest.raises(ExtractError, match="pages; backend limit"):
        preflight_pdf(p, max_bytes=10_000_000, max_pages=2)


def test_preflight_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(ExtractError, match="not found"):
        preflight_pdf(tmp_path / "nope.pdf", max_bytes=10, max_pages=10)


def test_preflight_rejects_non_pdf_extension(tmp_path: Path) -> None:
    p = tmp_path / "file.png"
    p.write_bytes(b"x")
    with pytest.raises(ExtractError, match="Expected a .pdf"):
        preflight_pdf(p, max_bytes=10, max_pages=10)
