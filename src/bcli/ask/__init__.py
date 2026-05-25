"""``bcli.ask`` — second-opinion oracle (Part 2).

A single ``bcli ask "<question>"`` call bundles the operator's
recent failing context (last-error, http-tail, profile metadata,
describe excerpt) via :mod:`bcli.context`, ships it to a
configured LLM backend, and prints a free-text explanation. NOT a
loop; one shot, one answer.

Built-in backends: ``null`` (default), ``claude``, ``openai``.
Third-party backends register by import path
``module.path:ClassName`` exactly like the extract layer.
"""

from __future__ import annotations

from bcli.ask._factory import get_asker
from bcli.ask._protocol import AskAnswer, AskBackend, NullAsker
from bcli.ask._providers import (
    ENTRYPOINT_GROUP as CONTEXT_PROVIDERS_ENTRYPOINT_GROUP,
    collect_extra_context,
    discover_providers,
)

__all__ = [
    "AskAnswer",
    "AskBackend",
    "CONTEXT_PROVIDERS_ENTRYPOINT_GROUP",
    "NullAsker",
    "collect_extra_context",
    "discover_providers",
    "get_asker",
]
