"""YAML-defined extraction schemas.

A schema is a YAML file (matching the saved-queries precedent) that
describes:

1. What to extract — a list of typed, described fields the model fills.
2. How to talk to the model — a free-form ``prompt`` plus a ``list`` flag
   for one-vs-many records per PDF.
3. How to land in BC — an ``output`` block naming the endpoint, the
   field map, and an optional parent-id placeholder so the generated
   batch.yaml has a ``${{ params.X }}`` the user fills in before
   ``batch run``.

The schema is *data* — non-developers can add a new doc type by
dropping a new YAML file into ``~/.config/bcli/extract/schemas/``
and pointing at it from ``[extract] schemas_dir``. Compiled to a
JSON Schema at extract time for the backend's structured-output tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from bcli.errors import ExtractError

_FIELD_TYPES = {"string", "integer", "number", "boolean", "date"}
_JSON_TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "date": "string",  # ISO 8601 string in JSON Schema; description nudges format
}


class FieldDef(BaseModel):
    """One extracted field — name, type, description, requiredness."""

    type: Literal["string", "integer", "number", "boolean", "date"] = "string"
    description: str = ""
    required: bool = False
    enum: list[Any] | None = None

    def to_json_schema(self) -> dict[str, Any]:
        """Render this field as a JSON Schema property."""
        prop: dict[str, Any] = {"type": _JSON_TYPE_MAP[self.type]}
        desc = self.description
        if self.type == "date" and "ISO" not in desc.upper():
            desc = (desc + " Format: ISO 8601 date (YYYY-MM-DD).").strip()
        if desc:
            prop["description"] = desc
        if self.enum is not None:
            prop["enum"] = self.enum
        return prop


class OutputMap(BaseModel):
    """How an extracted record maps to a BC batch step.

    ``field_map`` keys are BC field names; values are extracted field
    names. ``parent_param`` (if set) emits a ``${{ params.<name> }}``
    placeholder for ``parent_field`` in the generated batch.yaml — the
    operator fills in the systemId of the parent record (e.g. the
    invoice the line items belong to) before ``batch run``.
    """

    endpoint: str
    field_map: dict[str, str] = Field(default_factory=dict)
    parent_field: str | None = None
    parent_param: str | None = None
    action: Literal["post", "patch"] = "post"
    constants: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ExtractSchema(BaseModel):
    """A loaded extraction schema."""

    name: str
    description: str = ""
    prompt: str
    list: bool = False
    fields: dict[str, FieldDef]
    source_page_field: str = "source_pages"
    output: OutputMap

    model_config = {"extra": "forbid"}

    def to_json_schema(self) -> dict[str, Any]:
        """Compile to a JSON Schema for the backend's structured-output tool.

        Always emits a top-level ``records`` array — even for ``list:
        false`` schemas (where we expect exactly 1 record) — so the
        backend code has one shape to handle. The Claude backend wraps
        this as a tool's ``input_schema``.
        """
        record_props: dict[str, Any] = {}
        required: list[str] = []
        for fname, fdef in self.fields.items():
            record_props[fname] = fdef.to_json_schema()
            if fdef.required:
                required.append(fname)

        # Always include source page array; nudges the model to cite pages
        record_props[self.source_page_field] = {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "description": (
                "1-indexed PDF page numbers where the values for this record "
                "were read. List every page that contributed."
            ),
        }
        required.append(self.source_page_field)

        record_schema = {
            "type": "object",
            "properties": record_props,
            "required": required,
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "records": {"type": "array", "items": record_schema},
            },
            "required": ["records"],
            "additionalProperties": False,
        }


def load_schema(path: Path) -> ExtractSchema:
    """Load and validate a schema YAML file.

    Raises :class:`ExtractError` with the offending path when validation
    fails — surfaced verbatim to the user, no Pydantic dump.
    """
    if not path.is_file():
        raise ExtractError(f"Schema file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ExtractError(f"Schema YAML parse failure in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ExtractError(
            f"Schema file {path} must be a YAML mapping, got "
            f"{type(raw).__name__}."
        )

    try:
        schema = ExtractSchema.model_validate(raw)
    except Exception as e:  # noqa: BLE001 — pydantic raises ValidationError but be defensive
        raise ExtractError(
            f"Schema validation failed for {path}: {e}"
        ) from e

    _validate_field_types(schema, path)
    _validate_field_map(schema, path)
    return schema


def _validate_field_types(schema: ExtractSchema, path: Path) -> None:
    for fname, fdef in schema.fields.items():
        if fdef.type not in _FIELD_TYPES:
            raise ExtractError(
                f"Schema {path}: field {fname!r} has unsupported type "
                f"{fdef.type!r}. Allowed: {sorted(_FIELD_TYPES)}."
            )


def _validate_field_map(schema: ExtractSchema, path: Path) -> None:
    unknown = [
        src for src in schema.output.field_map.values()
        if src not in schema.fields
    ]
    if unknown:
        raise ExtractError(
            f"Schema {path}: output.field_map references unknown extracted "
            f"fields {unknown}. Defined fields: {sorted(schema.fields.keys())}."
        )
    if schema.output.parent_field and not schema.output.parent_param:
        raise ExtractError(
            f"Schema {path}: output.parent_field set but parent_param missing. "
            "Both must be set together (parent_param names the workflow "
            "parameter the operator fills in before batch run)."
        )


def discover_schemas(schemas_dir: Path) -> dict[str, Path]:
    """Map ``<slug>`` → file path for every ``*.yaml`` under ``schemas_dir``.

    Returns an empty dict if the directory doesn't exist (legitimate
    when no schemas are installed yet) — callers should surface that as
    "no schemas configured" rather than an error.
    """
    if not schemas_dir.is_dir():
        return {}
    out: dict[str, Path] = {}
    for child in sorted(schemas_dir.iterdir()):
        if child.suffix.lower() in (".yaml", ".yml") and child.is_file():
            out[child.stem] = child
    return out
