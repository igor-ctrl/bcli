"""AskBackend protocol + always-available NullAsker.

Mirror of :mod:`bcli.extract._protocol` so the factory dispatch
(``bcli.ask._factory``) can stay byte-identical in shape. A backend
takes a :class:`bcli.context.ContextBundle` plus the operator's
free-text question and returns a textual answer with optional
diagnostics (token counts, model id).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from bcli.config._model import AskConfig
    from bcli.context import ContextBundle


@dataclass(frozen=True)
class AskAnswer:
    """The output of one :meth:`AskBackend.ask` call.

    ``answer`` is the rendered text the CLI prints. ``model`` and
    ``input_tokens`` / ``output_tokens`` are best-effort diagnostics —
    backends report them when the underlying SDK does. ``warnings``
    surfaces any non-fatal note (e.g. "context bundle truncated to
    fit token budget").
    """

    answer: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class AskBackend(Protocol):
    """Structural type every ask backend satisfies."""

    is_active: bool

    def ask(self, *, question: str, bundle: "ContextBundle") -> AskAnswer: ...


class NullAsker:
    """Zero-overhead backend used when no backend is configured.

    The CLI surfaces this with a "set [ask] backend = 'claude' …"
    message so the user knows why they got an empty reply.
    """

    is_active: bool = False

    @classmethod
    def from_config(cls, config: "AskConfig") -> "NullAsker":  # noqa: ARG003
        return cls()

    def ask(  # noqa: ARG002
        self, *, question: str, bundle: "ContextBundle"
    ) -> AskAnswer:
        return AskAnswer(
            answer="",
            warnings=[
                "No ask backend configured. Set [ask] backend = 'claude' "
                "in ~/.config/bcli/config.toml and install bc-cli[ask]."
            ],
        )


__all__ = ["AskAnswer", "AskBackend", "NullAsker"]
