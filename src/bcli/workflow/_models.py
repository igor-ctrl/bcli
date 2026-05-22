"""Workflow data models — step definitions, results, and execution context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ─── Pydantic models (YAML → validated structure) ────────────────────


class ParamDef(BaseModel):
    """Declared workflow parameter with optional default."""

    description: str = ""
    default: Any = None
    required: bool = True


class StepDef(BaseModel):
    """A single step as declared in a workflow YAML file.

    Steps may specify the HTTP verb under either of two keys:

    * ``action: post`` — the original spelling, lowercased.
    * ``method: POST`` — alias kept because authors copy-pasting OData
      examples (especially bound-action invocations) reach for ``method``
      out of habit. The model lowercases the value before assigning it
      to ``action``.

    Both keys at once are rejected to keep YAML files unambiguous.
    """

    name: str = ""
    action: Literal["get", "post", "patch", "delete"] = "get"
    endpoint: str
    data: dict[str, Any] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    id: str | None = None
    etag: str = "*"

    @model_validator(mode="before")
    @classmethod
    def _accept_method_alias(cls, data: Any) -> Any:
        """Translate ``method:`` → ``action:`` before field validation.

        Runs in ``mode="before"`` so the ``Literal[...]`` validator on
        ``action`` sees the normalised lowercase value and the unknown
        ``method`` field is consumed (it would otherwise raise an
        ``extra inputs`` error under ``model_config = ConfigDict(extra=
        "forbid")`` — kept open so existing YAML stays valid, but the
        validator still needs to drop the alias).
        """
        if not isinstance(data, dict):
            return data
        if "method" not in data:
            return data
        method_raw = data["method"]
        # Default to checking against the literal set the field accepts.
        # Anything else gets normalised and passed through so pydantic's
        # ``Literal[...]`` validator raises with a clear message.
        if isinstance(method_raw, str):
            method_norm = method_raw.lower()
        else:
            method_norm = method_raw
        if "action" in data:
            raise ValueError(
                "Step declares both 'action' and 'method'. Pick one key — "
                "'action' is the canonical spelling, 'method' is an alias "
                "accepted for OData copy-paste habits."
            )
        # Strip the alias and inject the normalised action.
        normalised = {k: v for k, v in data.items() if k != "method"}
        normalised["action"] = method_norm
        return normalised


class WorkflowDef(BaseModel):
    """Top-level workflow definition parsed from YAML."""

    name: str = ""
    params: dict[str, ParamDef] | None = None
    steps: list[StepDef] = Field(min_length=1)


# ─── Runtime types (execution state) ────────────────────────────────


@dataclass(frozen=True)
class StepResult:
    """Immutable record of a completed step's output."""

    name: str
    action: str
    endpoint: str
    status: Literal["ok", "error", "skipped"]
    data: dict[str, Any] | list[dict[str, Any]] = field(default_factory=dict)
    error: str | None = None


@dataclass
class WorkflowContext:
    """Accumulated state during workflow execution."""

    step_results: dict[str, StepResult] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def set_result(self, name: str, result: StepResult) -> None:
        self.step_results[name] = result

    def get_result(self, name: str) -> StepResult | None:
        return self.step_results.get(name)
