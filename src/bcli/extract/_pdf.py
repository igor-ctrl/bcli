"""PDF preflight checks.

Anthropic's PDF document blocks have hard limits — 32 MB / 100 pages
per document at time of writing. Validating up front beats a 30-second
roundtrip to a 400 response. The numbers are configurable through
:class:`ExtractConfig` so they can be bumped when Anthropic relaxes
the limits without a code change.
"""

from __future__ import annotations

from pathlib import Path

from bcli.errors import ExtractError


def preflight_pdf(
    path: Path,
    *,
    max_bytes: int,
    max_pages: int,
) -> int:
    """Return page count if the PDF fits within the backend limits, else raise.

    Raises :class:`ExtractError` with an actionable next-step message
    (split the PDF with ``pdftk`` / ``qpdf`` / a follow-up bcli command)
    when the file is too big.
    """
    if not path.is_file():
        raise ExtractError(f"PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ExtractError(
            f"Expected a .pdf file, got {path.name}. Other formats can be "
            "added by writing a custom backend; only PDF is built-in."
        )

    size = path.stat().st_size
    if size > max_bytes:
        mb = size / (1024 * 1024)
        cap_mb = max_bytes / (1024 * 1024)
        raise ExtractError(
            f"PDF is {mb:.1f} MB; backend limit is {cap_mb:.0f} MB. "
            f"Split it before extracting:\n"
            f"  qpdf --split-pages=50 {path.name!s} split-%d.pdf\n"
            "Then run bcli extract on each split file."
        )

    try:
        pages = _count_pages(path)
    except Exception as e:  # noqa: BLE001
        raise ExtractError(f"Could not read PDF {path}: {e}") from e

    if pages > max_pages:
        raise ExtractError(
            f"PDF has {pages} pages; backend limit is {max_pages}. "
            f"Split it before extracting:\n"
            f"  qpdf --split-pages={max_pages} {path.name!s} split-%d.pdf\n"
            "Then run bcli extract on each split file."
        )
    return pages


def _count_pages(path: Path) -> int:
    """Lazy import of pypdf — only needed when extract is actually used."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return len(reader.pages)
