"""Workflow data models — step definitions, results, and execution context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


# ─── Pydantic models (YAML → validated structure) ────────────────────


class ParamDef(BaseModel):
    """Declared workflow parameter with optional default."""

    description: str = ""
    default: Any = None
    required: bool = True


class StepDef(BaseModel):
    """A single step as declared in a workflow YAML file."""

    name: str = ""
    action: Literal["get", "post", "patch", "delete"] = "get"
    endpoint: str
    data: dict[str, Any] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    id: str | None = None
    etag: str = "*"


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
