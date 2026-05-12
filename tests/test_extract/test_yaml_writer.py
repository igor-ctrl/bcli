"""Tests for the batch.yaml + sidecar JSON emitters."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from bcli.extract._protocol import ExtractedRecord, ExtractionResult
from bcli.extract._schema import load_schema
from bcli.extract._yaml_writer import render_batch_yaml, render_sidecar_json


def _schema(tmp_path: Path, body: str) -> "object":
    p = tmp_path / "schema.yaml"
    p.write_text(body, encoding="utf-8")
    return load_schema(p)


def test_batch_yaml_round_trips_through_yaml_loader(tmp_path: Path) -> None:
    schema = _schema(
        tmp_path,
        """
name: "8130"
prompt: "extract one record per tag"
list: true
fields:
  part_no:
    type: string
    required: true
    description: "block 7"
  serial_no:
    type: string
    required: true
    description: "block 13"
output:
  endpoint: trackedParts
  action: post
  parent_field: parentEngineId
  parent_param: parent_engine_id
  field_map:
    partNo: part_no
    serialNo: serial_no
  constants:
    documentType: "8130-3"
""",
    )
    result = ExtractionResult(
        schema_name="8130",
        records=[
            ExtractedRecord(
                fields={"part_no": "PN-1", "serial_no": "SN-A"},
                source_pages=(1, 2),
            ),
            ExtractedRecord(
                fields={"part_no": "PN-2", "serial_no": "SN-B"},
                source_pages=(3,),
            ),
        ],
        model="claude-sonnet-4-6",
    )

    rendered = render_batch_yaml(
        result, schema, source_pdf=tmp_path / "blades.pdf"
    )
    parsed = yaml.safe_load(rendered)

    assert parsed["name"].startswith("Load 8130 from")
    assert "parent_engine_id" in parsed["params"]
    assert len(parsed["steps"]) == 2

    first = parsed["steps"][0]
    assert first["action"] == "post"
    assert first["endpoint"] == "trackedParts"
    assert first["data"]["partNo"] == "PN-1"
    assert first["data"]["serialNo"] == "SN-A"
    assert first["data"]["documentType"] == "8130-3"
    # Parent linkage emitted as ${{ params.X }} reference
    assert first["data"]["parentEngineId"] == "${{ params.parent_engine_id }}"


def test_batch_yaml_omits_params_when_no_parent_param(tmp_path: Path) -> None:
    schema = _schema(
        tmp_path,
        """
name: "loose"
prompt: "..."
fields:
  part_no:
    type: string
    required: true
    description: "x"
output:
  endpoint: trackedParts
  field_map:
    partNo: part_no
""",
    )
    result = ExtractionResult(
        schema_name="loose",
        records=[ExtractedRecord(fields={"part_no": "X"})],
    )
    parsed = yaml.safe_load(
        render_batch_yaml(result, schema, source_pdf=tmp_path / "f.pdf")
    )
    assert "params" not in parsed


def test_sidecar_json_has_source_pages_and_warnings(tmp_path: Path) -> None:
    schema = _schema(
        tmp_path,
        """
name: "list-only"
prompt: "..."
list: true
fields:
  part_no:
    type: string
    required: true
    description: "x"
output:
  endpoint: trackedParts
  field_map:
    partNo: part_no
""",
    )
    result = ExtractionResult(
        schema_name="list-only",
        records=[
            ExtractedRecord(
                fields={"part_no": "PN"},
                source_pages=(7,),
                raw='{"part_no": "PN"}',
            )
        ],
        model="claude-sonnet-4-6",
        warnings=["one warning"],
        input_tokens=42,
        output_tokens=7,
    )

    sidecar = json.loads(
        render_sidecar_json(result, schema, source_pdf=tmp_path / "x.pdf")
    )
    assert sidecar["model"] == "claude-sonnet-4-6"
    assert sidecar["usage"] == {"input_tokens": 42, "output_tokens": 7}
    assert sidecar["records"][0]["source_pages"] == [7]
    assert sidecar["warnings"] == ["one warning"]
