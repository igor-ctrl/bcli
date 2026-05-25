"""Anthropic Claude backend for ``bcli ask`` (Part 2 / oracle).

The bundle renders as a Markdown user-turn alongside a small system
prompt. We deliberately do NOT use tool-use here — the answer is a
free-text explanation the human reads, not a structured payload.

Anthropic SDK is loaded lazily — installing bcli without the
``[ask]`` extra still works; ``from_config`` raises a clear error
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


logger = logging.getLogger("bcli.ask.claude")


_SYSTEM_PROMPT = (
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


class ClaudeAskError(BCLIError):
    """Raised by ClaudeAsker when the API call fails irrecoverably."""


class ClaudeAsker:
    """Anthropic Claude backend — text in, text out."""

    is_active: bool = True

    DEFAULT_MODEL = "claude-sonnet-4-6"
    DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client = client

    @classmethod
    def from_config(cls, config: "AskConfig") -> "ClaudeAsker":
        import os

        env_name = config.api_key_env or cls.DEFAULT_API_KEY_ENV
        api_key = os.environ.get(env_name, "")
        if not api_key:
            raise ClaudeAskError(
                f"{env_name} not set. Export your Anthropic API key "
                f"and re-run: export {env_name}=sk-ant-..."
            )
        model = config.model or cls.DEFAULT_MODEL
        return cls(
            api_key=api_key,
            model=model,
            max_tokens=config.max_tokens or 1024,
        )

    # ─── ask ─────────────────────────────────────────────────────────

    def ask(self, *, question: str, bundle: "ContextBundle") -> AskAnswer:
        try:
            import anthropic
        except ModuleNotFoundError as exc:
            raise ClaudeAskError(
                "anthropic SDK not installed. Run "
                "`pip install bc-cli[ask-claude]` and try again."
            ) from exc

        client = self._client or anthropic.Anthropic(api_key=self._api_key)
        prompt = _render_prompt(bundle=bundle, question=question)
        try:
            resp = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # noqa: BLE001
            raise ClaudeAskError(
                f"Anthropic API call failed: {exc}"
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
    """Compose the user-turn body.

    The bundle's ``to_prompt_text`` already organises the sections in
    priority order. We just frame it so the model knows what's
    deliberate context vs the operator's question — and ask it to
    answer the question, not just summarise the bundle.
    """
    rendered_bundle = bundle.to_prompt_text().strip()
    return (
        "## Bundle (operator's recent bcli context)\n\n"
        f"{rendered_bundle}\n\n"
        "## Operator's question\n\n"
        f"{question.strip() or '(none — explain what happened)'}\n"
    )


def _extract_text(resp: Any) -> str:
    """Pull plain text out of Anthropic's content list.

    The SDK returns ``content=[ContentBlock(...), ...]`` where each
    block has a ``type`` (``text`` / ``tool_use`` / …) and a
    ``text`` attribute when applicable. We concatenate every text
    block — if the model emitted multiple, the human sees them
    joined by blank lines.
    """
    content = getattr(resp, "content", None) or []
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", "") != "text":
            continue
        t = getattr(block, "text", None)
        if isinstance(t, str) and t.strip():
            parts.append(t.strip())
    return "\n\n".join(parts) if parts else ""


__all__ = ["ClaudeAskError", "ClaudeAsker"]
