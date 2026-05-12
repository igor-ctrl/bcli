"""OpenAI vision extraction backend.

Uploads the PDF through the Files API, then asks the Responses API for a
JSON-schema-constrained answer (structured outputs). The Responses API
is the SDK path that natively handles PDF ``input_file`` content blocks
alongside ``input_text`` instructions — same shape as Claude's
``document`` block but on OpenAI's surface.

OpenAI SDK is loaded lazily — installing bcli without the
``[extract-openai]`` extra still works; ``from_config`` raises a clear
error only when the backend is actually selected.

Uploaded files are deleted after extraction so a long-running operator
account doesn't accumulate stale PDF uploads.
"""

from __future__ import annotations

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

logger = logging.getLogger("bcli.extract.openai")

_RESPONSE_FORMAT_NAME = "emit_records"
_SYSTEM_PROMPT = (
    "You are a precise document-extraction engine. The user will give you "
    "a PDF and an extraction task. Read the PDF, find every record the task "
    "describes, and respond in the JSON shape the response_format demands. "
    "Cite the 1-indexed PDF page number(s) for each record in the "
    "source_pages field. If a required field is illegible or missing on the "
    "page, OMIT the record rather than guessing — never fabricate identifiers "
    "(part numbers, serial numbers, invoice numbers, account numbers, etc.). "
    "Extracted records may drive downstream writes with real-world "
    "consequences; precision > recall."
)


class OpenAIExtractor:
    """OpenAI vision backend for PDF extraction.

    Uses ``responses.create`` with a Files-API upload + structured-output
    JSON schema. Strict mode is enabled so the response shape matches the
    compiled :meth:`ExtractSchema.to_json_schema` output exactly.
    """

    is_active: bool = True

    DEFAULT_MODEL = "gpt-5"
    DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_pdf_bytes: int,
        max_pdf_pages: int,
        max_output_tokens: int,
        organization: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_pdf_bytes = max_pdf_bytes
        self._max_pdf_pages = max_pdf_pages
        self._max_output_tokens = max_output_tokens
        self._organization = organization
        self._base_url = base_url
        self._client = client

    @classmethod
    def from_config(cls, config: "ExtractConfig") -> "OpenAIExtractor":
        import os

        env_name = config.api_key_env or cls.DEFAULT_API_KEY_ENV
        api_key = os.environ.get(env_name, "")
        if not api_key:
            raise ExtractError(
                f"{env_name} not set. Export your OpenAI API key "
                f"and re-run: export {env_name}=sk-..."
            )

        try:
            import openai  # noqa: F401
        except ImportError as e:
            raise ExtractError(
                "openai SDK not installed. Install the OpenAI extra:\n"
                "  uv pip install -e \".[extract-openai]\"\n"
                "or\n  pip install 'bc-cli[extract-openai]'"
            ) from e

        return cls(
            api_key=api_key,
            model=config.model or cls.DEFAULT_MODEL,
            max_pdf_bytes=config.max_pdf_bytes,
            max_pdf_pages=config.max_pdf_pages,
            max_output_tokens=config.max_output_tokens,
            organization=config.openai_organization,
            base_url=config.openai_base_url,
        )

    def extract(
        self, pdf_path: Path, schema: "ExtractSchema"
    ) -> ExtractionResult:
        pages = preflight_pdf(
            pdf_path,
            max_bytes=self._max_pdf_bytes,
            max_pages=self._max_pdf_pages,
        )

        client = self._get_client()
        user_prompt = self._build_user_prompt(schema, page_count=pages)

        uploaded_id: str | None = None
        try:
            uploaded = client.files.create(
                file=open(pdf_path, "rb"),  # noqa: SIM115 — SDK takes a file handle
                purpose="user_data",
            )
            uploaded_id = self._extract_id(uploaded)
            if not uploaded_id:
                raise ExtractError(
                    "OpenAI files.create returned no id — cannot proceed."
                )

            response = client.responses.create(
                model=self._model,
                max_output_tokens=self._max_output_tokens,
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": _SYSTEM_PROMPT}],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_file", "file_id": uploaded_id},
                            {"type": "input_text", "text": user_prompt},
                        ],
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": _RESPONSE_FORMAT_NAME,
                        "schema": schema.to_json_schema(),
                        "strict": True,
                    }
                },
            )
        except ExtractError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ExtractError(f"OpenAI extract call failed: {e}") from e
        finally:
            if uploaded_id is not None:
                self._best_effort_delete(client, uploaded_id)

        return self._parse_response(response, schema)

    # ─── Internals ────────────────────────────────────────────────────

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        import openai

        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._organization:
            kwargs["organization"] = self._organization
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = openai.OpenAI(**kwargs)
        return self._client

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
            f"Respond ONLY in the JSON shape demanded by response_format."
        )

    def _parse_response(
        self, response: Any, schema: "ExtractSchema"
    ) -> ExtractionResult:
        payload = _extract_json_payload(response)
        if payload is None:
            raise ExtractError(
                "OpenAI response did not contain a JSON payload. The Responses "
                "API may have refused the request or returned an unsupported "
                "shape. Re-run with --debug for the raw response."
            )

        records_raw = payload.get("records", [])
        if not isinstance(records_raw, list):
            raise ExtractError(
                f"emit_records returned non-list records: "
                f"{type(records_raw).__name__}"
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
            input_tokens=_usage_field(usage, "input_tokens", "prompt_tokens"),
            output_tokens=_usage_field(usage, "output_tokens", "completion_tokens"),
            warnings=warnings,
        )

    @staticmethod
    def _extract_id(obj: Any) -> str | None:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get("id")
        return getattr(obj, "id", None)

    @staticmethod
    def _best_effort_delete(client: Any, file_id: str) -> None:
        try:
            client.files.delete(file_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not delete OpenAI file %s: %s", file_id, e)


def _extract_json_payload(response: Any) -> dict[str, Any] | None:
    """Pull the JSON payload out of an OpenAI Responses API result.

    Handles the SDK shape variations we care about — the parsed-output
    field, the ``output_text`` convenience accessor, and the raw
    ``output[].content[].text`` walk — so callers don't have to.
    """
    # 1) ``response.output_parsed`` (newer SDK convenience for structured outputs).
    parsed = getattr(response, "output_parsed", None)
    if isinstance(parsed, dict):
        return parsed

    # 2) ``response.output_text`` (concatenated text content) → JSON string.
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # 3) Walk ``response.output[].content[].text``.
    for item in getattr(response, "output", None) or []:
        content = getattr(item, "content", None) or (
            item.get("content") if isinstance(item, dict) else None
        )
        if not content:
            continue
        for block in content:
            block_text = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else None
            )
            if isinstance(block_text, str) and block_text.strip():
                try:
                    return json.loads(block_text)
                except json.JSONDecodeError:
                    continue
    return None


def _usage_field(usage: Any, *names: str) -> int:
    """Read the first matching usage attribute (Responses API → Chat Completions)."""
    if usage is None:
        return 0
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
        if isinstance(usage, dict) and name in usage:
            v = usage[name]
            if isinstance(v, int):
                return v
    return 0
