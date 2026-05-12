"""OpenAI backend tests — fully mocked, no network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bcli.config._model import ExtractConfig
from bcli.errors import ExtractError
from bcli.extract._openai import OpenAIExtractor
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


class _Files:
    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None
        self.deleted: list[str] = []
        self.next_id = "file-123"

    def create(self, *, file: Any, purpose: str) -> Any:
        self.created = {"purpose": purpose, "bytes": file.read()}
        return SimpleNamespace(id=self.next_id)

    def delete(self, file_id: str) -> None:
        self.deleted.append(file_id)


class _Responses:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        return self.response


class _FakeOpenAI:
    def __init__(self, response: Any) -> None:
        self.files = _Files()
        self.responses = _Responses(response)


def _response_via_output_text(records: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(
        model="gpt-5",
        output_text=json.dumps({"records": records}),
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


def _response_via_parsed(records: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(
        model="gpt-5",
        output_parsed={"records": records},
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


def _response_via_output_walk(records: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(
        model="gpt-5",
        output=[
            SimpleNamespace(
                content=[
                    SimpleNamespace(text=json.dumps({"records": records}))
                ]
            )
        ],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


@pytest.fixture
def extractor() -> OpenAIExtractor:
    return OpenAIExtractor(
        api_key="fake",
        model="gpt-5",
        max_pdf_bytes=10_000_000,
        max_pdf_pages=100,
        max_output_tokens=2000,
    )


def test_extract_parses_output_text(
    tmp_path: Path, extractor: OpenAIExtractor
) -> None:
    pdf = tmp_path / "blades.pdf"
    _make_pdf(pdf)
    schema = _list_schema(tmp_path)

    fake = _FakeOpenAI(
        _response_via_output_text(
            [
                {"part_no": "PN-1", "source_pages": [1]},
                {"part_no": "PN-2", "source_pages": [2]},
            ]
        )
    )
    extractor._client = fake

    result = extractor.extract(pdf, schema)

    assert len(result.records) == 2
    assert result.records[0].fields == {"part_no": "PN-1"}
    assert result.records[0].source_pages == (1,)
    assert result.input_tokens == 100
    assert result.output_tokens == 20

    # Files API was called and the file was cleaned up afterwards
    assert fake.files.created is not None
    assert fake.files.created["purpose"] == "user_data"
    assert fake.files.deleted == ["file-123"]

    # Responses API was called with the structured-output format
    sent = fake.responses.last_kwargs
    assert sent is not None
    assert sent["model"] == "gpt-5"
    assert sent["text"]["format"]["type"] == "json_schema"
    assert sent["text"]["format"]["strict"] is True
    # Input has the input_file referencing the uploaded id
    user_content = sent["input"][1]["content"]
    assert user_content[0] == {"type": "input_file", "file_id": "file-123"}
    assert user_content[1]["type"] == "input_text"


def test_extract_parses_output_parsed(
    tmp_path: Path, extractor: OpenAIExtractor
) -> None:
    pdf = tmp_path / "blades.pdf"
    _make_pdf(pdf)
    schema = _list_schema(tmp_path)
    extractor._client = _FakeOpenAI(
        _response_via_parsed([{"part_no": "PN-X", "source_pages": [3]}])
    )
    result = extractor.extract(pdf, schema)
    assert result.records[0].fields == {"part_no": "PN-X"}
    assert result.records[0].source_pages == (3,)


def test_extract_parses_output_walk(
    tmp_path: Path, extractor: OpenAIExtractor
) -> None:
    pdf = tmp_path / "blades.pdf"
    _make_pdf(pdf)
    schema = _list_schema(tmp_path)
    extractor._client = _FakeOpenAI(
        _response_via_output_walk([{"part_no": "PN-W", "source_pages": [5]}])
    )
    result = extractor.extract(pdf, schema)
    assert result.records[0].fields == {"part_no": "PN-W"}


def test_extract_oversize_pdf_short_circuits(tmp_path: Path) -> None:
    pdf = tmp_path / "big.pdf"
    _make_pdf(pdf, pages=200)
    schema = _list_schema(tmp_path)
    fake = _FakeOpenAI(_response_via_output_text([]))
    extractor = OpenAIExtractor(
        api_key="fake",
        model="gpt-5",
        max_pdf_bytes=10_000_000,
        max_pdf_pages=50,
        max_output_tokens=2000,
        client=fake,
    )
    with pytest.raises(ExtractError, match="pages; backend limit"):
        extractor.extract(pdf, schema)
    assert fake.files.created is None
    assert fake.responses.last_kwargs is None


def test_extract_missing_payload_raises(
    tmp_path: Path, extractor: OpenAIExtractor
) -> None:
    pdf = tmp_path / "blades.pdf"
    _make_pdf(pdf)
    schema = _list_schema(tmp_path)
    extractor._client = _FakeOpenAI(
        SimpleNamespace(model="gpt-5", output_text="", output=[], usage=None)
    )
    with pytest.raises(ExtractError, match="did not contain a JSON payload"):
        extractor.extract(pdf, schema)


def test_uploaded_file_deleted_even_on_response_failure(
    tmp_path: Path, extractor: OpenAIExtractor
) -> None:
    pdf = tmp_path / "blades.pdf"
    _make_pdf(pdf)
    schema = _list_schema(tmp_path)

    class _BadResponses:
        last_kwargs = None

        def create(self, **kwargs):
            raise RuntimeError("api 500")

    fake = _FakeOpenAI(None)
    fake.responses = _BadResponses()  # type: ignore[assignment]
    extractor._client = fake

    with pytest.raises(ExtractError, match="OpenAI extract call failed"):
        extractor.extract(pdf, schema)
    # The uploaded file is still cleaned up
    assert fake.files.deleted == ["file-123"]


def test_list_false_schema_warns_on_extra_records(
    tmp_path: Path, extractor: OpenAIExtractor
) -> None:
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
    extractor._client = _FakeOpenAI(
        _response_via_output_text(
            [
                {"part_no": "PN-1", "source_pages": [1]},
                {"part_no": "PN-2", "source_pages": [2]},
            ]
        )
    )
    result = extractor.extract(pdf, schema)
    assert len(result.records) == 1
    assert any("list: false" in w for w in result.warnings)


def test_from_config_missing_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = ExtractConfig(backend="openai")
    with pytest.raises(ExtractError, match="OPENAI_API_KEY not set"):
        OpenAIExtractor.from_config(cfg)


def test_from_config_applies_defaults(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = ExtractConfig(backend="openai")  # blank model, blank api_key_env
    extractor = OpenAIExtractor.from_config(cfg)
    assert extractor._model == OpenAIExtractor.DEFAULT_MODEL
    assert extractor._api_key == "sk-test"


def test_from_config_honors_explicit_model_and_env(monkeypatch) -> None:
    monkeypatch.setenv("MY_OAI_KEY", "sk-other")
    cfg = ExtractConfig(backend="openai", model="gpt-4o", api_key_env="MY_OAI_KEY")
    extractor = OpenAIExtractor.from_config(cfg)
    assert extractor._model == "gpt-4o"
    assert extractor._api_key == "sk-other"
