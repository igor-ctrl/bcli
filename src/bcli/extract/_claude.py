"""Claude vision extraction backend.

Sends the PDF as a base64-encoded ``document`` content block alongside a
schema-derived tool definition; Claude returns a tool-use call whose
``input`` is the structured records. Tool-use forces the model into a
JSON shape that matches the schema's :meth:`ExtractSchema.to_json_schema`
output, so the backend doesn't have to parse free-form JSON from text.

Anthropic SDK is loaded lazily — installing bcli without the
``[extract]`` extra still works; ``from_config`` raises a clear error
only when the backend is actually selected.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bcli.errors import ExtractError
from bcli.extract._pdf import preflight_pdf
from bcli.extract._protocol import ExtractedRecord, ExtractionResult

if TYPE_CHECKING:
    from bcli.config._model import ExtractConfig
    from bcli.extract._schema import ExtractSchema

logger = logging.getLogger("bcli.extract.claude")

_TOOL_NAME = "emit_records"
_SYSTEM_PROMPT = (
    "You are a precise document-extraction engine. The user will give you "
    "a PDF and an extraction task. Read the PDF, find every record the task "
    "describes, and call the emit_records tool exactly once with all records. "
    "Cite the 1-indexed PDF page number(s) for each record in the "
    "source_pages field. If a required field is illegible or missing on the "
    "page, OMIT the record rather than guessing — never fabricate identifiers "
    "(part numbers, serial numbers, invoice numbers, account numbers, etc.). "
    "Extracted records may drive downstream writes with real-world "
    "consequences; precision > recall."
)


class ClaudeExtractor:
    """Anthropic Claude vision backend for PDF extraction."""

    is_active: bool = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_pdf_bytes: int,
        max_pdf_pages: int,
        max_output_tokens: int,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_pdf_bytes = max_pdf_bytes
        self._max_pdf_pages = max_pdf_pages
        self._max_output_tokens = max_output_tokens
        self._client = client

    DEFAULT_MODEL = "claude-sonnet-4-6"
    DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"

    @classmethod
    def from_config(cls, config: "ExtractConfig") -> "ClaudeExtractor":
        import os

        env_name = config.api_key_env or cls.DEFAULT_API_KEY_ENV
        api_key = os.environ.get(env_name, "")
        if not api_key:
            raise ExtractError(
                f"{env_name} not set. Export your Anthropic API key "
                f"and re-run: export {env_name}=sk-ant-..."
            )

        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise ExtractError(
                "anthropic SDK not installed. Install the Claude extra:\n"
                "  uv pip install -e \".[extract-claude]\"\n"
                "or\n  pip install 'bc-cli[extract-claude]'"
            ) from e

        return cls(
            api_key=api_key,
            model=config.model or cls.DEFAULT_MODEL,
            max_pdf_bytes=config.max_pdf_bytes,
            max_pdf_pages=config.max_pdf_pages,
            max_output_tokens=config.max_output_tokens,
        )

    def extract(
        self, pdf_path: Path, schema: "ExtractSchema"
    ) -> ExtractionResult:
        pages = preflight_pdf(
            pdf_path,
            max_bytes=self._max_pdf_bytes,
            max_pages=self._max_pdf_pages,
        )
        pdf_b64 = base64.standard_b64encode(pdf_path.read_bytes()).decode("ascii")

        tool = {
            "name": _TOOL_NAME,
            "description": (
                f"Emit extracted records for: {schema.name}. "
                "Call exactly once with all records found in the PDF."
            ),
            "input_schema": schema.to_json_schema(),
        }
        user_prompt = self._build_user_prompt(schema, page_count=pages)

        try:
            response = self._invoke(pdf_b64=pdf_b64, tool=tool, user_prompt=user_prompt)
        except Exception as e:  # noqa: BLE001
            raise ExtractError(f"Claude extract call failed: {e}") from e

        return self._parse_response(response, schema)

    # ─── Internals ────────────────────────────────────────────────────

    def _build_user_prompt(self, schema: "ExtractSchema", *, page_count: int) -> str:
        plurality = (
            "There may be multiple records in this PDF — emit one entry per record."
            if schema.list
            else "There is exactly one record in this PDF."
        )
        return (
            f"Document: {schema.name} ({page_count} pages)\n"
            f"{schema.description}\n\n"
            f"Task:\n{schema.prompt}\n\n"
            f"{plurality}\n"
            f"Call the {_TOOL_NAME} tool with the extracted records."
        )

    def _invoke(
        self, *, pdf_b64: str, tool: dict[str, Any], user_prompt: str
    ) -> Any:
        client = self._client
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=self._api_key)
            self._client = client

        return client.messages.create(
            model=self._model,
            max_tokens=self._max_output_tokens,
            system=_SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": user_prompt},
                    ],
                }
            ],
        )

    def _parse_response(
        self, response: Any, schema: "ExtractSchema"
    ) -> ExtractionResult:
        tool_input = _find_tool_input(response, _TOOL_NAME)
        if tool_input is None:
            raise ExtractError(
                "Claude did not call the emit_records tool. This usually "
                "means the schema prompt is too vague or the PDF doesn't "
                "contain extractable content. Sharpen the prompt and retry."
            )

        records_raw = tool_input.get("records", [])
        if not isinstance(records_raw, list):
            raise ExtractError(
                f"emit_records returned non-list records: {type(records_raw).__name__}"
            )

        warnings: list[str] = []
        if not schema.list and len(records_raw) > 1:
            warnings.append(
                f"Schema declared list: false but extractor returned "
                f"{len(records_raw)} records; keeping the first."
            )
            records_raw = records_raw[:1]

        records: list[ExtractedRecord] = []
        for i, raw in enumerate(records_raw):
            if not isinstance(raw, dict):
                warnings.append(f"Record {i} not a dict; skipped.")
                continue
            source_pages = tuple(raw.pop(schema.source_page_field, []) or ())
            records.append(
                ExtractedRecord(
                    fields=raw,
                    source_pages=source_pages,
                    raw=json.dumps(raw, default=str),
                )
            )

        usage = getattr(response, "usage", None)
        return ExtractionResult(
            schema_name=schema.name,
            records=records,
            model=getattr(response, "model", self._model),
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            warnings=warnings,
        )


def _find_tool_input(response: Any, tool_name: str) -> dict[str, Any] | None:
    """Locate the tool_use block in an Anthropic response, robust to SDK shape."""
    content = getattr(response, "content", None)
    if content is None:
        return None
    for block in content:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type != "tool_use":
            continue
        name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        if name != tool_name:
            continue
        input_val = getattr(block, "input", None) or (
            block.get("input") if isinstance(block, dict) else None
        )
        if isinstance(input_val, dict):
            return input_val
    return None
