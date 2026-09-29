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
name: "vendor-invoice"
prompt: "extract one record per invoice line"
list: true
fields:
  item_no:
    type: string
    required: true
    description: "item number column"
  unit_of_measure:
    type: string
    required: true
    description: "unit of measure column"
output:
  endpoint: purchaseInvoiceLines
  action: post
  parent_field: documentId
  parent_param: purchase_invoice_id
  field_map:
    lineObjectNumber: item_no
    unitOfMeasureCode: unit_of_measure
  constants:
    lineType: "Item"
""",
    )
    result = ExtractionResult(
        schema_name="vendor-invoice",
        records=[
            ExtractedRecord(
                fields={"item_no": "1896-S", "unit_of_measure": "PCS"},
                source_pages=(1, 2),
            ),
            ExtractedRecord(
                fields={"item_no": "1906-S", "unit_of_measure": "BOX"},
                source_pages=(3,),
            ),
        ],
        model="claude-sonnet-4-6",
    )

    rendered = render_batch_yaml(
        result, schema, source_pdf=tmp_path / "invoice.pdf"
    )
    parsed = yaml.safe_load(rendered)

    assert parsed["name"].startswith("Load vendor-invoice from")
    assert "purchase_invoice_id" in parsed["params"]
    assert len(parsed["steps"]) == 2

    first = parsed["steps"][0]
    assert first["action"] == "post"
    assert first["endpoint"] == "purchaseInvoiceLines"
    assert first["data"]["lineObjectNumber"] == "1896-S"
    assert first["data"]["unitOfMeasureCode"] == "PCS"
    assert first["data"]["lineType"] == "Item"
    # Parent linkage emitted as ${{ params.X }} reference
    assert first["data"]["documentId"] == "${{ params.purchase_invoice_id }}"


def test_batch_yaml_omits_params_when_no_parent_param(tmp_path: Path) -> None:
    schema = _schema(
        tmp_path,
        """
name: "loose"
prompt: "..."
fields:
  item_no:
    type: string
    required: true
    description: "x"
output:
  endpoint: purchaseInvoiceLines
  field_map:
    lineObjectNumber: item_no
""",
    )
    result = ExtractionResult(
        schema_name="loose",
        records=[ExtractedRecord(fields={"item_no": "X"})],
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
  item_no:
    type: string
    required: true
    description: "x"
output:
  endpoint: purchaseInvoiceLines
  field_map:
    lineObjectNumber: item_no
""",
    )
    result = ExtractionResult(
        schema_name="list-only",
        records=[
            ExtractedRecord(
                fields={"item_no": "1896-S"},
                source_pages=(7,),
                raw='{"item_no": "1896-S"}',
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
