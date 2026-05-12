"""PDF / document → structured-data extraction layer.

Plugs into ``bcli extract <pdf> --schema <name>`` to drive AI extraction
through a pluggable backend (Claude vision by default; custom backends
loadable by ``module.path:ClassName`` import path).

Pipeline:

    PDF → ExtractorBackend.extract(pdf_path, schema)
        → ExtractionResult (records + per-field source pages)
        → batch.yaml + <pdf>.extracted.json (traceability sidecar)
        → human review against the source PDF
        → bcli batch run … --dry-run --profile sandbox
        → bcli batch run … --profile sandbox  (real sandbox write)
        → bcli batch run … --profile production  (only after sandbox passes)

The extraction never POSTs to BC directly. Reviewers compare the sidecar
against the source PDF before running the batch — important for any data
whose downstream consequences exceed the cost of one review pass
(regulated records, financial postings, anything where a wrong
identifier matters in the real world).
"""

from bcli.extract._factory import get_extractor
from bcli.extract._protocol import (
    ExtractedRecord,
    ExtractionResult,
    ExtractorBackend,
    NullExtractor,
)
from bcli.extract._schema import ExtractSchema, FieldDef, OutputMap, load_schema

__all__ = [
    "ExtractedRecord",
    "ExtractSchema",
    "ExtractionResult",
    "ExtractorBackend",
    "FieldDef",
    "NullExtractor",
    "OutputMap",
    "get_extractor",
    "load_schema",
]
