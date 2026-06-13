"""ToolRegistry: tier classification, describe round-trip, MCP parity.

The agent registry mirrors :mod:`bcli_mcp._tool_generator` in shape
(``bcli_<path>`` names, the same JSON-Schema type map) but cannot import
it (SDK must not depend on the MCP package). These tests pin the parity
so the two never silently drift.
"""

from __future__ import annotations

from bcli.agent.tools._registry import (
    ToolRegistry,
    _build_input_schema,
    _path_to_tool_name,
)
from bcli_mcp._tool_generator import (
    _build_input_schema as mcp_build_input_schema,
    _path_to_tool_name as mcp_path_to_tool_name,
)


def test_default_registry_has_read_and_write_tiers() -> None:
    reg = ToolRegistry.default()
    read = {s.name for s in reg.read_specs()}
    write = {s.name for s in reg.write_specs()}
    assert "bcli_get" in read
    assert "bcli_endpoint_search" in read
    assert "bcli_post" in write
    assert "bcli_delete" in write
    assert read.isdisjoint(write)


def test_plan_mode_swaps_writes_for_draft_batch() -> None:
    reg = ToolRegistry.default()
    plan_names = reg.tool_names(plan_mode=True)
    assert "draft_batch" in plan_names
    assert "bcli_post" not in plan_names
    assert "bcli_delete" not in plan_names
    # reads survive
    assert "bcli_get" in plan_names


def test_name_mapping_matches_mcp() -> None:
    for path in (["get"], ["endpoint", "search"], ["batch", "run"], ["attach", "upload"]):
        assert _path_to_tool_name(path) == mcp_path_to_tool_name(path)


def test_input_schema_matches_mcp_for_get() -> None:
    positionals = [
        {"name": "endpoint", "type": "str", "required": True},
        {"name": "record_id", "type": "str", "required": False},
    ]
    options = [
        {"name": "--filter", "type": "str"},
        {"name": "--top", "type": "int", "limits": {"default": 50, "minimum": 1, "maximum": 1000}},
    ]
    ours = _build_input_schema(positionals, options)
    theirs = mcp_build_input_schema(positionals, options)
    assert ours == theirs


def test_from_describe_filters_to_supported_paths() -> None:
    payload = {
        "commands": [
            {"path": ["get"], "summary": "g", "positionals": [
                {"name": "endpoint", "type": "str", "required": True}], "options": []},
            {"path": ["post"], "summary": "p", "positionals": [], "options": [
                {"name": "--data", "type": "str", "required": True}]},
            # Not in supported sets — must be excluded.
            {"path": ["auth", "login"], "summary": "x", "positionals": [], "options": []},
            {"path": ["registry", "import"], "summary": "y", "positionals": [], "options": []},
        ]
    }
    reg = ToolRegistry.from_describe(payload)
    names = set(reg.tool_names())
    assert names == {"bcli_get", "bcli_post"}


def test_from_describe_empty_falls_back_to_default() -> None:
    reg = ToolRegistry.from_describe({"commands": []})
    assert "bcli_get" in reg.tool_names()


def test_curated_overlay_applied_on_describe_rebuild() -> None:
    payload = {"commands": [
        {"path": ["get"], "summary": "terse cli help", "positionals": [
            {"name": "endpoint", "type": "str", "required": True}], "options": []},
    ]}
    reg = ToolRegistry.from_describe(payload)
    spec = reg.get("bcli_get")
    assert spec is not None
    # The curated overlay (richer LLM description) wins over the terse summary.
    assert "Discovery-first" in spec.description
