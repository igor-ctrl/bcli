"""Extractor backend protocol + always-available NullExtractor.

Any class satisfying :class:`ExtractorBackend` can be plugged in as the
``[extract] backend`` for bcli. Built-ins live in this package; third-party
backends are loaded by ``module.path:ClassName`` import path.

A backend MUST expose:

* ``is_active`` — boolean. ``False`` for :class:`NullExtractor`, ``True``
  for any backend that can actually call out to an extraction service.
* ``extract(pdf_path, schema)`` — return an :class:`ExtractionResult`. May
  raise :class:`bcli.errors.ExtractError` with a user-actionable message.
* ``from_config(cls, config)`` — classmethod returning a configured
  instance, used by :func:`bcli.extract.get_extractor`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from bcli.config._model import ExtractConfig
    from bcli.extract._schema import ExtractSchema


@dataclass(frozen=True)
class ExtractedRecord:
    """One record (row) returned by the extractor.

    ``fields`` is the schema-validated payload. ``source_pages`` lists
    every PDF page (1-indexed) that contributed to this record — the
    reviewer uses this to jump straight to the relevant page when
    sanity-checking the extracted values against the source.
    ``raw`` holds the unvalidated text the model returned, so the
    sidecar can show what was actually said before coercion to the
    schema's types.
    """

    fields: dict[str, Any]
    source_pages: tuple[int, ...] = ()
    raw: str = ""


@dataclass(frozen=True)
class ExtractionResult:
    """Top-level result of one extract call.

    A schema with ``list: false`` returns ``records`` of length 1. A
    schema with ``list: true`` (e.g. one PDF holding many line items)
    returns N records. ``model`` and ``input_tokens`` / ``output_tokens``
    are populated for backends that report them and surface in the
    sidecar for audit / cost-tracking.
    """

    schema_name: str
    records: list[ExtractedRecord] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class ExtractorBackend(Protocol):
    """Structural type for extraction backends."""

    is_active: bool

    def extract(self, pdf_path: Path, schema: "ExtractSchema") -> ExtractionResult: ...


class NullExtractor:
    """Zero-overhead extractor returning no records.

    Used when no backend is configured. The CLI surfaces this with a
    clear "set [extract] backend = 'claude'" message so the user knows
    why nothing came back.
    """

    is_active: bool = False

    @classmethod
    def from_config(cls, config: "ExtractConfig") -> "NullExtractor":  # noqa: ARG003
        return cls()

    def extract(  # noqa: ARG002
        self, pdf_path: Path, schema: "ExtractSchema"
    ) -> ExtractionResult:
        return ExtractionResult(
            schema_name=schema.name,
            warnings=[
                "No extraction backend configured. Set [extract] backend = 'claude' "
                "in ~/.config/bcli/config.toml and install bc-cli[extract]."
            ],
        )
