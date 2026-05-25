"""OpenAI backend for ``bcli ask`` — Responses API, free-text output.

Uses ``responses.create`` with a simple text-only payload — no
structured output, no tool use. The answer is a free-form
explanation the operator reads in the terminal.

OpenAI SDK is loaded lazily; ``from_config`` raises a clear error
only when the backend is actually selected.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bcli.ask._protocol import AskAnswer
from bcli.errors import BCLIError

if TYPE_CHECKING:
    from bcli.config._model import AskConfig
    from bcli.context import ContextBundle


logger = logging.getLogger("bcli.ask.openai")


_INSTRUCTIONS = (
    "You are bcli's oracle — a second-opinion assistant invoked when "
    "the operator's first attempt against Business Central failed or "
    "looks suspicious. Read the failing-context bundle below and:\n"
    "  - Explain the *most likely* root cause in 1-3 sentences.\n"
    "  - Cite specific evidence (HTTP status, error class, BC message, "
    "filter expression).\n"
    "  - Suggest the concrete next bcli command(s) the operator should "
    "run. Do NOT recommend writes (POST/PATCH/DELETE) unless the "
    "operator asked for them.\n"
    "  - If the bundle is truncated, say what's missing — don't "
    "speculate beyond the evidence.\n"
    "Keep the answer tight (<300 words). Markdown is rendered in the "
    "terminal, so use fenced code blocks for commands."
)


class OpenAIAskError(BCLIError):
    """Raised by OpenAIAsker when the API call fails irrecoverably."""


class OpenAIAsker:
    """OpenAI Responses API backend — text in, text out."""

    is_active: bool = True

    DEFAULT_MODEL = "gpt-5"
    DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        base_url: str | None = None,
        organization: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._base_url = base_url
        self._organization = organization
        self._client = client

    @classmethod
    def from_config(cls, config: "AskConfig") -> "OpenAIAsker":
        import os

        env_name = config.api_key_env or cls.DEFAULT_API_KEY_ENV
        api_key = os.environ.get(env_name, "")
        if not api_key:
            raise OpenAIAskError(
                f"{env_name} not set. Export your OpenAI API key "
                f"and re-run: export {env_name}=sk-..."
            )
        return cls(
            api_key=api_key,
            model=config.model or cls.DEFAULT_MODEL,
            max_tokens=config.max_tokens or 1024,
            base_url=getattr(config, "base_url", None) or None,
            organization=getattr(config, "organization", None) or None,
        )

    def ask(self, *, question: str, bundle: "ContextBundle") -> AskAnswer:
        try:
            import openai
        except ModuleNotFoundError as exc:
            raise OpenAIAskError(
                "openai SDK not installed. Run "
                "`pip install bc-cli[ask-openai]` and try again."
            ) from exc

        if self._client is not None:
            client = self._client
        else:
            kwargs: dict[str, Any] = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._organization:
                kwargs["organization"] = self._organization
            client = openai.OpenAI(**kwargs)

        prompt = _render_prompt(bundle=bundle, question=question)
        try:
            resp = client.responses.create(
                model=self._model,
                instructions=_INSTRUCTIONS,
                input=prompt,
                max_output_tokens=self._max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            raise OpenAIAskError(
                f"OpenAI API call failed: {exc}"
            ) from exc

        text = _extract_text(resp)
        usage = getattr(resp, "usage", None)
        return AskAnswer(
            answer=text,
            model=str(getattr(resp, "model", self._model)),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )


def _render_prompt(*, bundle: "ContextBundle", question: str) -> str:
    rendered_bundle = bundle.to_prompt_text().strip()
    return (
        "## Bundle (operator's recent bcli context)\n\n"
        f"{rendered_bundle}\n\n"
        "## Operator's question\n\n"
        f"{question.strip() or '(none — explain what happened)'}\n"
    )


def _extract_text(resp: Any) -> str:
    """Pull plain text out of OpenAI's Responses output.

    The SDK exposes a convenience accessor ``output_text`` that
    returns the concatenated text. Older SDK versions or non-stream
    fallbacks may need walking ``output`` blocks.
    """
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", "") in {"output_text", "text"}:
                t = getattr(block, "text", None)
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
    return "\n\n".join(parts) if parts else ""


__all__ = ["OpenAIAskError", "OpenAIAsker"]
