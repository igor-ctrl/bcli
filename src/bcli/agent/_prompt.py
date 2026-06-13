"""System prompt assembly for the bcli agent.

Order (pi.dev lesson: short prompt, strong tools):

1. Base instructions — who the agent is, discovery-first tool habits,
   the write-safety contract.
2. BC.md memory (when ``[agent] memory = true`` and a file exists).
3. The redacted context bundle (profile snapshot + recent errors) via
   :meth:`bcli.context.ContextBundle.to_prompt_text` — token-budgeted by
   the context layer itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bcli.context import ContextBundle

BASE_INSTRUCTIONS = """\
You are the bcli agent: an operator's assistant for Microsoft Dynamics \
365 Business Central, working exclusively through bcli tools.

Rules:
- Discovery first. If you are not certain an endpoint or field name \
exists, call bcli_endpoint_search / bcli_endpoint_fields before \
querying. Never guess field names in filters.
- Prefer narrow queries: use top, select, and filter instead of \
fetching everything.
- Writes are serious. State what you are about to change and why \
before calling a write tool. A refusal result means the operator \
declined — do not retry; ask how to proceed.
- When plan mode is active, draft changes with draft_batch instead of \
writing. The operator reviews and runs the batch.
- You cannot run interactive commands (auth login, config init). Tell \
the operator to run them in their own terminal.
- Be concise. Answer with the data, cite record counts, and show \
amounts with their currency.
"""


def build_system_prompt(
    *,
    memory_text: str = "",
    bundle: "ContextBundle | None" = None,
    plan_mode: bool = False,
) -> str:
    parts: list[str] = [BASE_INSTRUCTIONS]
    if plan_mode:
        parts.append(
            "PLAN MODE is ON: write tools are unavailable; propose all "
            "changes through draft_batch."
        )
    if memory_text.strip():
        parts.append("## Operator memory (BC.md)\n\n" + memory_text.strip())
    if bundle is not None:
        text = bundle.to_prompt_text().strip()
        if text:
            parts.append("## Session context\n\n" + text)
    return "\n\n".join(parts)


__all__ = ["BASE_INSTRUCTIONS", "build_system_prompt"]
