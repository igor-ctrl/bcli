"""ToolRegistry — the agent's tool surface, derived from describe shape.

The registry is built from describe-shaped command entries (see
:mod:`bcli.agent.tools._definitions`) and classifies each tool into a
``read`` or ``write`` tier. In plan mode the write tier is replaced by
the single ``draft_batch`` tool — the model can only *propose* changes
as a reviewable batch YAML.

Schema derivation intentionally matches
:mod:`bcli_mcp._tool_generator` (``bcli_<path_underscored>`` names,
JSON Schema from positionals + options with limits carried through).
The MCP module cannot be imported here — the package contract is
"no imports from ``bcli_mcp`` into ``bcli``" — so the small mapping is
mirrored locally and pinned by parity tests in
``tests/test_agent/test_registry.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from bcli.agent.tools._definitions import (
    BUILTIN_DEFINITIONS,
    CURATED_OVERLAY,
    DRAFT_BATCH_TOOL,
    READ_PATHS,
    WRITE_PATHS,
)

Tier = Literal["read", "write", "plan"]


# describe ``type`` strings → JSON Schema primitive types
# (mirror of bcli_mcp._tool_generator._TYPE_MAP — parity-tested).
_TYPE_MAP: dict[str, str] = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "bool": "boolean",
    "boolean": "boolean",
    "float": "number",
    "path": "string",
}


def _path_to_tool_name(path: list[str] | tuple[str, ...]) -> str:
    """``["batch", "run"]`` → ``"bcli_batch_run"`` (mirror of bcli_mcp)."""
    flat = "_".join(p.replace("-", "_") for p in path)
    return f"bcli_{flat}"


def _flag_to_kwarg(flag: str) -> str:
    """``"--file-name"`` → ``"file_name"`` (mirror of bcli_mcp)."""
    return flag.lstrip("-").replace("-", "_")


def _option_property(opt: dict[str, Any]) -> dict[str, Any]:
    json_type = _TYPE_MAP.get(opt.get("type", "string"), "string")
    prop: dict[str, Any] = {"type": json_type}
    limits = opt.get("limits") or {}
    for key in ("default", "minimum", "maximum"):
        if key in limits:
            prop[key] = limits[key]
    return prop


def _build_input_schema(
    positionals: list[dict[str, Any]], options: list[dict[str, Any]],
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pos in positionals:
        properties[pos["name"]] = {
            "type": _TYPE_MAP.get(pos.get("type", "string"), "string"),
        }
        if pos.get("required"):
            required.append(pos["name"])
    for opt in options:
        kwarg = _flag_to_kwarg(opt["name"])
        properties[kwarg] = _option_property(opt)
        if opt.get("required"):
            required.append(kwarg)
    return {"type": "object", "properties": properties, "required": required}


@dataclass(frozen=True)
class ToolSpec:
    """One agent tool, projected from a describe-shaped command entry."""

    name: str
    description: str
    path: tuple[str, ...]
    tier: Tier
    positionals: tuple[dict[str, Any], ...] = ()
    options: tuple[dict[str, Any], ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def is_write(self) -> bool:
        return self.tier == "write"


def _spec_from_entry(entry: dict[str, Any], tier: Tier) -> ToolSpec:
    positionals = list(entry.get("positionals") or [])
    options = list(entry.get("options") or [])
    path = tuple(entry["path"])
    description = CURATED_OVERLAY.get(path) or entry.get("summary", "") or ""
    return ToolSpec(
        name=_path_to_tool_name(entry["path"]),
        description=description,
        path=path,
        tier=tier,
        positionals=tuple(positionals),
        options=tuple(options),
        input_schema=_build_input_schema(positionals, options),
    )


_DRAFT_BATCH_SPEC = ToolSpec(
    name="draft_batch",
    description=DRAFT_BATCH_TOOL["summary"],
    path=tuple(DRAFT_BATCH_TOOL["path"]),
    tier="plan",
    positionals=tuple(DRAFT_BATCH_TOOL["positionals"]),
    options=tuple(DRAFT_BATCH_TOOL["options"]),
    input_schema=_build_input_schema(
        list(DRAFT_BATCH_TOOL["positionals"]), [],
    ),
)


class ToolRegistry:
    """The agent's tool surface: read tier + gated write tier.

    Build with :meth:`default` (built-in curated definitions) or
    :meth:`from_describe` (live ``bcli describe --format json`` payload,
    filtered to the supported paths, curated overlay applied).
    """

    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = list(specs)

    # ── constructors ──────────────────────────────────────────────────

    @classmethod
    def default(cls) -> "ToolRegistry":
        specs = [
            _spec_from_entry(e, "read" if tuple(e["path"]) in READ_PATHS else "write")
            for e in BUILTIN_DEFINITIONS
        ]
        return cls(specs)

    @classmethod
    def from_describe(cls, payload: dict[str, Any]) -> "ToolRegistry":
        """Build from a live describe payload.

        Only commands whose path is in the supported read/write sets are
        projected; everything else (interactive commands, plumbing) is
        excluded by construction. Falls back to :meth:`default` when the
        payload carries no usable commands.
        """
        specs: list[ToolSpec] = []
        for cmd in payload.get("commands", []):
            path = tuple(cmd.get("path") or ())
            if path in READ_PATHS:
                specs.append(_spec_from_entry(cmd, "read"))
            elif path in WRITE_PATHS:
                specs.append(_spec_from_entry(cmd, "write"))
        if not specs:
            return cls.default()
        return cls(specs)

    # ── views ─────────────────────────────────────────────────────────

    def specs(self, *, plan_mode: bool = False) -> list[ToolSpec]:
        """The active tool list. Plan mode swaps writes for draft_batch."""
        if not plan_mode:
            return list(self._specs)
        out = [s for s in self._specs if s.tier == "read"]
        out.append(_DRAFT_BATCH_SPEC)
        return out

    def read_specs(self) -> list[ToolSpec]:
        return [s for s in self._specs if s.tier == "read"]

    def write_specs(self) -> list[ToolSpec]:
        return [s for s in self._specs if s.tier == "write"]

    def get(self, name: str) -> ToolSpec | None:
        if name == _DRAFT_BATCH_SPEC.name:
            return _DRAFT_BATCH_SPEC
        for s in self._specs:
            if s.name == name:
                return s
        return None

    def tool_names(self, *, plan_mode: bool = False) -> list[str]:
        return [s.name for s in self.specs(plan_mode=plan_mode)]


__all__ = ["Tier", "ToolRegistry", "ToolSpec"]
