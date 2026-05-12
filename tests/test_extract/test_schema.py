"""Tests for the YAML extraction schema loader and JSON Schema compiler."""

from __future__ import annotations

from pathlib import Path

import pytest

from bcli.errors import ExtractError
from bcli.extract._schema import discover_schemas, load_schema


def _write_schema(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "schema.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_minimal_schema(tmp_path: Path) -> None:
    path = _write_schema(
        tmp_path,
        """
name: "Test"
prompt: "extract things"
fields:
  part_no:
    type: string
    required: true
    description: "the part"
output:
  endpoint: trackedParts
  field_map:
    partNo: part_no
""",
    )
    schema = load_schema(path)

    assert schema.name == "Test"
    assert schema.list is False
    assert schema.fields["part_no"].required is True
    assert schema.output.endpoint == "trackedParts"


def test_load_schema_compiles_to_json_schema_with_source_pages(tmp_path: Path) -> None:
    path = _write_schema(
        tmp_path,
        """
name: "8130"
prompt: "..."
list: true
fields:
  serial_no:
    type: string
    required: true
    description: "Block 13"
output:
  endpoint: trackedParts
  field_map:
    serialNo: serial_no
""",
    )
    js = load_schema(path).to_json_schema()

    record_props = js["properties"]["records"]["items"]["properties"]
    assert "serial_no" in record_props
    assert record_props["source_pages"]["type"] == "array"
    assert record_props["source_pages"]["items"]["type"] == "integer"
    assert "source_pages" in js["properties"]["records"]["items"]["required"]


def test_field_map_referencing_unknown_field_rejected(tmp_path: Path) -> None:
    path = _write_schema(
        tmp_path,
        """
name: "bad"
prompt: "..."
fields:
  part_no:
    type: string
    required: true
    description: "the part"
output:
  endpoint: trackedParts
  field_map:
    serialNo: serial_no   # not declared
""",
    )
    with pytest.raises(ExtractError, match="unknown extracted fields"):
        load_schema(path)


def test_parent_field_without_parent_param_rejected(tmp_path: Path) -> None:
    path = _write_schema(
        tmp_path,
        """
name: "bad parent"
prompt: "..."
fields:
  part_no:
    type: string
    required: true
    description: "x"
output:
  endpoint: trackedParts
  parent_field: parentEngineId
  field_map:
    partNo: part_no
""",
    )
    with pytest.raises(ExtractError, match="parent_param missing"):
        load_schema(path)


def test_extra_output_keys_rejected(tmp_path: Path) -> None:
    """A typo in the output block (e.g. ``parent_filed``) must not silently pass."""
    path = _write_schema(
        tmp_path,
        """
name: "typo"
prompt: "..."
fields:
  part_no:
    type: string
    required: true
    description: "x"
output:
  endpoint: trackedParts
  parent_filed: parentEngineId   # typo: filed → field
  parent_param: parent_engine_id
  field_map:
    partNo: part_no
""",
    )
    with pytest.raises(ExtractError):
        load_schema(path)


def test_extra_top_level_keys_rejected(tmp_path: Path) -> None:
    path = _write_schema(
        tmp_path,
        """
name: "x"
prompt: "..."
fields:
  a:
    type: string
    required: true
    description: "x"
output:
  endpoint: e
  field_map:
    a: a
mystery_key: oops
""",
    )
    with pytest.raises(ExtractError):
        load_schema(path)


def test_discover_schemas_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert discover_schemas(tmp_path / "nope") == {}


def test_discover_schemas_finds_yaml_files(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("x", encoding="utf-8")
    (tmp_path / "b.yml").write_text("x", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")

    found = discover_schemas(tmp_path)
    assert set(found.keys()) == {"a", "b"}


def test_date_field_gets_iso_hint(tmp_path: Path) -> None:
    path = _write_schema(
        tmp_path,
        """
name: "d"
prompt: "..."
fields:
  cert_date:
    type: date
    required: true
    description: "Certification date"
output:
  endpoint: e
  field_map:
    certDate: cert_date
""",
    )
    js = load_schema(path).to_json_schema()
    desc = js["properties"]["records"]["items"]["properties"]["cert_date"]["description"]
    assert "ISO 8601" in desc
