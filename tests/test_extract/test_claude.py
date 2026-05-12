"""Claude backend tests — fully mocked, no network."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bcli.config._model import ExtractConfig
from bcli.errors import ExtractError
from bcli.extract._claude import ClaudeExtractor
from bcli.extract._schema import load_schema


def _make_pdf(path: Path, pages: int = 1) -> None:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as f:
        writer.write(f)


def _schema(tmp_path: Path, body: str):
    p = tmp_path / "schema.yaml"
    p.write_text(body, encoding="utf-8")
    return load_schema(p)


def _list_schema(tmp_path: Path):
    return _schema(
        tmp_path,
        """
name: "tags"
prompt: "extract"
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


class _FakeAnthropic:
    """Stand-in for anthropic.Anthropic — captures the request and replays
    a programmable response."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.last_kwargs: dict[str, Any] | None = None

    @property
    def messages(self) -> "_FakeAnthropic":
        return self

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        return self.response


def _tool_use_block(input_payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_use", name="emit_records", input=input_payload
    )


def _response(records: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        model="claude-sonnet-4-6",
        content=[_tool_use_block({"records": records})],
        usage=SimpleNamespace(input_tokens=42, output_tokens=7),
    )


def test_extract_parses_tool_use_response(tmp_path: Path) -> None:
    pdf = tmp_path / "blades.pdf"
    _make_pdf(pdf)
    schema = _list_schema(tmp_path)

    fake = _FakeAnthropic(
        _response(
            [
                {"part_no": "PN-1", "source_pages": [1]},
                {"part_no": "PN-2", "source_pages": [2]},
            ]
        )
    )
    extractor = ClaudeExtractor(
        api_key="fake",
        model="claude-sonnet-4-6",
        max_pdf_bytes=10_000_000,
        max_pdf_pages=100,
        max_output_tokens=2000,
        client=fake,
    )

    result = extractor.extract(pdf, schema)

    assert len(result.records) == 2
    assert result.records[0].fields == {"part_no": "PN-1"}
    assert result.records[0].source_pages == (1,)
    assert result.input_tokens == 42
    assert result.output_tokens == 7
    assert result.model == "claude-sonnet-4-6"

    sent = fake.last_kwargs
    assert sent is not None
    assert sent["model"] == "claude-sonnet-4-6"
    assert sent["tool_choice"] == {"type": "tool", "name": "emit_records"}
    assert sent["tools"][0]["name"] == "emit_records"
    # Document content block included with base64 PDF
    doc_block = sent["messages"][0]["content"][0]
    assert doc_block["type"] == "document"
    assert doc_block["source"]["media_type"] == "application/pdf"
    assert isinstance(doc_block["source"]["data"], str)


def test_extract_oversize_pdf_short_circuits_before_call(tmp_path: Path) -> None:
    pdf = tmp_path / "big.pdf"
    _make_pdf(pdf, pages=200)
    schema = _list_schema(tmp_path)
    fake = _FakeAnthropic(_response([]))

    extractor = ClaudeExtractor(
        api_key="fake",
        model="claude-sonnet-4-6",
        max_pdf_bytes=10_000_000,
        max_pdf_pages=50,
        max_output_tokens=2000,
        client=fake,
    )

    with pytest.raises(ExtractError, match="pages; backend limit"):
        extractor.extract(pdf, schema)
    assert fake.last_kwargs is None  # never called out


def test_missing_tool_use_raises(tmp_path: Path) -> None:
    pdf = tmp_path / "blades.pdf"
    _make_pdf(pdf)
    schema = _list_schema(tmp_path)
    fake = _FakeAnthropic(
        SimpleNamespace(
            model="claude-sonnet-4-6",
            content=[SimpleNamespace(type="text", text="I refuse")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )
    )
    extractor = ClaudeExtractor(
        api_key="fake",
        model="claude-sonnet-4-6",
        max_pdf_bytes=10_000_000,
        max_pdf_pages=100,
        max_output_tokens=2000,
        client=fake,
    )

    with pytest.raises(ExtractError, match="did not call the emit_records tool"):
        extractor.extract(pdf, schema)


def test_list_false_schema_warns_on_extra_records(tmp_path: Path) -> None:
    pdf = tmp_path / "single.pdf"
    _make_pdf(pdf)
    schema = _schema(
        tmp_path,
        """
name: "single"
prompt: "one"
list: false
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
    fake = _FakeAnthropic(
        _response(
            [
                {"part_no": "PN-1", "source_pages": [1]},
                {"part_no": "PN-2", "source_pages": [2]},
            ]
        )
    )
    extractor = ClaudeExtractor(
        api_key="fake",
        model="claude-sonnet-4-6",
        max_pdf_bytes=10_000_000,
        max_pdf_pages=100,
        max_output_tokens=2000,
        client=fake,
    )

    result = extractor.extract(pdf, schema)
    assert len(result.records) == 1
    assert any("list: false" in w for w in result.warnings)


def test_from_config_missing_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = ExtractConfig(backend="claude")
    with pytest.raises(ExtractError, match="ANTHROPIC_API_KEY not set"):
        ClaudeExtractor.from_config(cfg)


def test_from_config_applies_defaults(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg = ExtractConfig(backend="claude")  # blank model, blank api_key_env
    extractor = ClaudeExtractor.from_config(cfg)
    assert extractor._model == ClaudeExtractor.DEFAULT_MODEL
    assert extractor._api_key == "sk-ant-test"
