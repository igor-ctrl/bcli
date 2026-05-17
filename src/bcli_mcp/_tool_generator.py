"""Dynamic MCP tool generator — parses ``bcli describe`` output.

Phase 5 of AIP v0.1. The contract is: any tool surface an MCP client sees
comes directly from what ``bcli describe --format json`` projects.
No hand-written tool schemas allowed. New CLI commands light up
automatically; deprecated ones disappear.

Each command in describe becomes one :class:`GeneratedTool`:

* ``name``         — ``bcli_<path_underscored>``
* ``description``  — the command's summary
* ``input_schema`` — JSON Schema built from ``positionals`` + ``options``
                     (with ``limits`` carried through for safety bounds)
* ``effects``      — passed through from describe (``["read"]`` /
                     ``["mutating"]``)
* ``emits_envelope`` — true iff the command declared
                     ``emits_result_envelope`` (mutating verbs do; read
                     verbs don't)
* ``build_argv(inputs)`` — turns a tool call's kwargs into the right
                     bcli CLI argv (positionals first, then ``--flag
                     value`` pairs; bool flags only when True)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# describe ``type`` strings → JSON Schema primitive types.
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


def _is_list_type(type_name: str) -> bool:
    """True if describe described a list-of-strings (or similar) input.

    ``bcli describe`` types these as ``list[str]``. The MCP tool surfaces
    them as a single string the agent fills with space-separated tokens
    (e.g. ``"batch run"`` for ``bcli describe batch run``). build_argv
    splits on whitespace at call time so the bcli subprocess gets the
    right argv shape.
    """
    return type_name.lower().startswith("list[")


def _path_to_tool_name(path: list[str]) -> str:
    """``["batch", "list-templates"]`` → ``"bcli_batch_list_templates"``."""
    flat = "_".join(p.replace("-", "_") for p in path)
    return f"bcli_{flat}"


def _flag_to_kwarg(flag: str) -> str:
    """``"--filter"`` → ``"filter"``; ``"--no-registry"`` → ``"no_registry"``."""
    return flag.lstrip("-").replace("-", "_")


def _option_to_input_property(opt: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON Schema property entry for one ``option`` entry."""
    json_type = _TYPE_MAP.get(opt.get("type", "string"), "string")
    prop: dict[str, Any] = {"type": json_type}
    limits = opt.get("limits") or {}
    if "default" in limits:
        prop["default"] = limits["default"]
    if "minimum" in limits:
        prop["minimum"] = limits["minimum"]
    if "maximum" in limits:
        prop["maximum"] = limits["maximum"]
    return prop


@dataclass(frozen=True)
class GeneratedTool:
    """One MCP tool, projected from a single describe command entry."""

    name: str
    description: str
    path: tuple[str, ...]
    positionals: tuple[dict[str, Any], ...]
    options: tuple[dict[str, Any], ...]
    effects: list[str]
    emits_envelope: bool
    input_schema: dict[str, Any] = field(default_factory=dict)

    def build_argv(self, inputs: dict[str, Any]) -> list[str]:
        """Translate a tool-call ``inputs`` dict into bcli CLI argv.

        Positionals come first in declared order; missing required
        positionals raise ``ValueError`` so the caller never ships
        malformed argv. Boolean flags emit only when truthy. Other
        flags emit ``--name <value>`` when the input is not ``None``.
        """
        argv: list[str] = list(self.path)

        # Positionals.
        for pos in self.positionals:
            name = pos["name"]
            value = inputs.get(name)
            if value is None:
                if pos.get("required"):
                    raise ValueError(
                        f"required positional '{name}' missing for {self.name}",
                    )
                continue
            # ``list[str]`` positionals are passed as a single
            # whitespace-separated string at the MCP surface and split
            # here at argv-build time. Lets ``bcli_describe`` accept
            # ``command_path="batch run"`` and produce
            # ``bcli describe batch run``.
            if _is_list_type(str(pos.get("type", ""))):
                if isinstance(value, list):
                    argv.extend(str(v) for v in value)
                else:
                    argv.extend(str(value).split())
                continue
            argv.append(str(value))

        # Options.
        for opt in self.options:
            kwarg = _flag_to_kwarg(opt["name"])
            value = inputs.get(kwarg)
            if value is None:
                continue
            json_type = _TYPE_MAP.get(opt.get("type", "string"), "string")
            if json_type == "boolean":
                if value:
                    argv.append(opt["name"])
                continue
            argv.extend([opt["name"], str(value)])
        return argv


def _build_input_schema(
    positionals: list[dict[str, Any]], options: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compose the MCP tool input schema."""
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
        properties[kwarg] = _option_to_input_property(opt)
        if opt.get("required"):
            required.append(kwarg)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def generate_tools(payload: dict[str, Any]) -> list[GeneratedTool]:
    """Yield one :class:`GeneratedTool` per command entry in ``payload``.

    Commands with ``effects: ["other"]`` are skipped — interactive
    commands (``auth login``, ``config init``) aren't scriptable.
    """
    out: list[GeneratedTool] = []
    for cmd in payload.get("commands", []):
        effects = list(cmd.get("effects", []))
        if effects == ["other"]:
            continue
        positionals = list(cmd.get("positionals") or [])
        options = list(cmd.get("options") or [])
        tool = GeneratedTool(
            name=_path_to_tool_name(cmd["path"]),
            description=cmd.get("summary", "") or "",
            path=tuple(cmd["path"]),
            positionals=tuple(positionals),
            options=tuple(options),
            effects=effects,
            emits_envelope=bool(cmd.get("emits_result_envelope", False)),
            input_schema=_build_input_schema(positionals, options),
        )
        out.append(tool)
    return out


__all__ = ["GeneratedTool", "generate_tools"]
