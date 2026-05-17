"""Tests for the dynamic tool generator.

The generator parses ``bcli describe --format json`` output and yields
one ``GeneratedTool`` per command entry. Mutating tools carry the
envelope contract: when invoked, the server must pass ``--result-out``
and return the envelope content as the tool result.

These tests cover the generator in isolation — no MCP server, no real
subprocess. Server integration is exercised in ``test_server_tools.py``.
"""

from __future__ import annotations

import pytest

from bcli_mcp._tool_generator import (
    _option_to_input_property,
    _path_to_tool_name,
    generate_tools,
)


# ── Fixtures ──────────────────────────────────────────────────────────


def _describe_payload(*commands: dict) -> dict:
    """Minimal describe payload with the given commands."""
    return {
        "version": "0.1",
        "tool": "bcli",
        "tool_version": "0.4.0",
        "profile": "dev",
        "commands": list(commands),
        "registry": {
            "tier_1_custom_count": 0,
            "tier_2_standard_enabled": True,
            "endpoints": [],
        },
        "profile_constraints": {
            "disable_writes": False,
            "disable_standard_api": False,
            "allowed_categories": None,
        },
    }


# ── _path_to_tool_name ────────────────────────────────────────────────


class TestPathToToolName:
    def test_single_word_path(self):
        assert _path_to_tool_name(["get"]) == "bcli_get"

    def test_two_word_path(self):
        assert _path_to_tool_name(["batch", "run"]) == "bcli_batch_run"

    def test_three_word_path(self):
        assert _path_to_tool_name(["attach", "upload"]) == "bcli_attach_upload"

    def test_hyphens_become_underscores(self):
        """MCP tool names must be a valid identifier so hyphens flatten."""
        assert _path_to_tool_name(["ai-context"]) == "bcli_ai_context"
        assert _path_to_tool_name(["batch", "list-templates"]) == "bcli_batch_list_templates"


# ── _option_to_input_property ─────────────────────────────────────────


class TestOptionToInputProperty:
    def test_string_option(self):
        prop = _option_to_input_property({"name": "--filter", "type": "str"})
        assert prop == {"type": "string"}

    def test_int_option(self):
        prop = _option_to_input_property({"name": "--top", "type": "int"})
        assert prop == {"type": "integer"}

    def test_bool_option(self):
        prop = _option_to_input_property({"name": "--yes", "type": "bool"})
        assert prop == {"type": "boolean"}

    def test_path_option(self):
        # Path falls through to string for JSON Schema purposes.
        prop = _option_to_input_property({"name": "--output", "type": "path"})
        assert prop == {"type": "string"}

    def test_unknown_type_falls_back_to_string(self):
        prop = _option_to_input_property({"name": "--mystery", "type": "weirdo"})
        assert prop == {"type": "string"}

    def test_limits_emitted_when_present(self):
        """Safety limits from describe carry into the JSON schema so an
        agent runtime can clamp before invoking the tool."""
        prop = _option_to_input_property({
            "name": "--top",
            "type": "int",
            "limits": {"default": 50, "minimum": 1, "maximum": 1000},
        })
        assert prop["type"] == "integer"
        assert prop["default"] == 50
        assert prop["minimum"] == 1
        assert prop["maximum"] == 1000


# ── generate_tools — read commands ────────────────────────────────────


class TestGenerateReadTools:
    def test_read_command_becomes_one_tool(self):
        payload = _describe_payload({
            "path": ["get"],
            "summary": "GET records from a Business Central entity.",
            "options": [
                {"name": "--filter", "type": "str", "validates": "odata-filter"},
                {"name": "--top", "type": "int", "limits": {"default": 50, "maximum": 1000}},
            ],
            "positionals": [
                {"name": "endpoint", "type": "str", "required": True},
                {"name": "record_id", "type": "str", "required": False},
            ],
            "effects": ["read"],
            "supported_formats": ["json", "table"],
        })
        tools = generate_tools(payload)
        assert len(tools) == 1
        t = tools[0]
        assert t.name == "bcli_get"
        assert t.effects == ["read"]
        assert t.emits_envelope is False
        # Schema captures both positionals and options.
        assert "endpoint" in t.input_schema["properties"]
        assert "record_id" in t.input_schema["properties"]
        assert "filter" in t.input_schema["properties"]
        assert "top" in t.input_schema["properties"]
        # Required list matches positionals[required=True].
        assert t.input_schema["required"] == ["endpoint"]
        # Safety limit propagated.
        assert t.input_schema["properties"]["top"]["maximum"] == 1000

    def test_summary_becomes_description(self):
        payload = _describe_payload({
            "path": ["company", "list"],
            "summary": "List companies on the active environment.",
            "options": [],
            "effects": ["read"],
            "supported_formats": ["json"],
        })
        tools = generate_tools(payload)
        assert tools[0].description == "List companies on the active environment."


# ── generate_tools — mutating commands ────────────────────────────────


class TestGenerateMutatingTools:
    def test_required_option_marked_required_in_schema(self):
        """``--data`` on ``bcli post`` is ``typer.Option(..., …)`` — i.e.
        required. Describe surfaces ``required: true``; the generator
        propagates that into JSON Schema's ``required`` array so an
        agent doesn't construct a missing-argument call."""
        payload = _describe_payload({
            "path": ["post"],
            "summary": "",
            "options": [
                {"name": "--data", "type": "str", "required": True},
                {"name": "--yes", "type": "bool"},
            ],
            "positionals": [
                {"name": "endpoint", "type": "str", "required": True},
            ],
            "effects": ["mutating"],
            "supported_formats": ["json"],
            "emits_result_envelope": True,
        })
        t = generate_tools(payload)[0]
        # Both the positional ``endpoint`` and the required option
        # ``data`` end up in the schema's required list.
        assert set(t.input_schema["required"]) == {"endpoint", "data"}

    def test_mutating_command_emits_envelope_flag(self):
        payload = _describe_payload({
            "path": ["post"],
            "summary": "POST (create) a new record.",
            "options": [
                {"name": "--data", "type": "str"},
                {"name": "--yes", "type": "bool"},
            ],
            "positionals": [
                {"name": "endpoint", "type": "str", "required": True},
            ],
            "effects": ["mutating"],
            "supported_formats": ["json"],
            "emits_result_envelope": True,
            "requires_confirmation": "production",
        })
        tools = generate_tools(payload)
        assert len(tools) == 1
        t = tools[0]
        assert t.name == "bcli_post"
        assert t.effects == ["mutating"]
        assert t.emits_envelope is True
        # The agent sees the same input shape as the CLI accepts.
        props = t.input_schema["properties"]
        assert "endpoint" in props
        assert "data" in props
        assert "yes" in props
        assert t.input_schema["required"] == ["endpoint"]

    def test_envelope_emitted_only_when_declared(self):
        """A command flagged ``effects: ["mutating"]`` without
        ``emits_result_envelope: true`` doesn't get the envelope wiring —
        we don't infer it from effects alone."""
        payload = _describe_payload({
            "path": ["weird"],
            "summary": "",
            "options": [],
            "effects": ["mutating"],
            "supported_formats": ["json"],
            # emits_result_envelope NOT set
        })
        t = generate_tools(payload)[0]
        assert t.emits_envelope is False


# ── generate_tools — all together ─────────────────────────────────────


class TestGenerateAllCommands:
    def test_tools_count_matches_command_count(self):
        payload = _describe_payload(
            {"path": ["get"], "summary": "", "options": [], "effects": ["read"],
             "supported_formats": ["json"]},
            {"path": ["post"], "summary": "", "options": [], "effects": ["mutating"],
             "supported_formats": ["json"], "emits_result_envelope": True},
            {"path": ["company", "list"], "summary": "", "options": [],
             "effects": ["read"], "supported_formats": ["json"]},
        )
        tools = generate_tools(payload)
        assert {t.name for t in tools} == {"bcli_get", "bcli_post", "bcli_company_list"}

    def test_invocations_skipped_for_other_effects(self):
        """Commands with effects=["other"] (e.g. ``config init``, ``auth
        login``) are surfaced read-style but never as mutating tools.
        Excluding them keeps the surface tight and avoids accidentally
        wrapping interactive prompts."""
        payload = _describe_payload(
            {"path": ["auth", "login"], "summary": "", "options": [],
             "effects": ["other"], "supported_formats": ["json"]},
        )
        # Default policy: skip "other".
        tools = generate_tools(payload)
        assert tools == []


# ── invocation arg-building ──────────────────────────────────────────


class TestBuildInvocationArgs:
    def test_read_tool_builds_get_argv(self):
        payload = _describe_payload({
            "path": ["get"],
            "summary": "",
            "options": [
                {"name": "--filter", "type": "str"},
                {"name": "--top", "type": "int"},
            ],
            "positionals": [
                {"name": "endpoint", "type": "str", "required": True},
            ],
            "effects": ["read"],
            "supported_formats": ["json"],
        })
        t = generate_tools(payload)[0]
        args = t.build_argv({"endpoint": "customers", "filter": "x eq 1", "top": 10})
        # Positionals come first in path order.
        assert args[0:2] == ["get", "customers"]
        # Option flags follow as --name value pairs.
        assert "--filter" in args
        assert args[args.index("--filter") + 1] == "x eq 1"
        assert args[args.index("--top") + 1] == "10"

    def test_skips_missing_optional_args(self):
        payload = _describe_payload({
            "path": ["get"],
            "summary": "",
            "options": [
                {"name": "--filter", "type": "str"},
                {"name": "--top", "type": "int"},
            ],
            "positionals": [
                {"name": "endpoint", "type": "str", "required": True},
            ],
            "effects": ["read"],
            "supported_formats": ["json"],
        })
        t = generate_tools(payload)[0]
        args = t.build_argv({"endpoint": "customers"})
        assert "--filter" not in args
        assert "--top" not in args

    def test_bool_flag_only_when_true(self):
        payload = _describe_payload({
            "path": ["post"],
            "summary": "",
            "options": [
                {"name": "--data", "type": "str"},
                {"name": "--yes", "type": "bool"},
            ],
            "positionals": [
                {"name": "endpoint", "type": "str", "required": True},
            ],
            "effects": ["mutating"],
            "supported_formats": ["json"],
            "emits_result_envelope": True,
        })
        t = generate_tools(payload)[0]
        args_true = t.build_argv({"endpoint": "vendors", "data": "{}", "yes": True})
        assert "--yes" in args_true
        args_false = t.build_argv({"endpoint": "vendors", "data": "{}", "yes": False})
        assert "--yes" not in args_false

    def test_list_positional_splits_whitespace_string(self):
        """``bcli describe`` accepts a list-of-strings positional. The MCP
        surface exposes it as a single string; build_argv splits at call
        time so the subprocess sees the right argv. Without this, an
        agent calling ``bcli_describe(command_path="batch run")`` would
        produce ``bcli describe "batch run"`` (one quoted token) — the
        CLI 4xxs because it expects two tokens."""
        payload = _describe_payload({
            "path": ["describe"],
            "summary": "",
            "options": [],
            "positionals": [
                {"name": "command_path", "type": "list[str]", "required": False},
            ],
            "effects": ["read"],
            "supported_formats": ["json"],
        })
        t = generate_tools(payload)[0]
        args = t.build_argv({"command_path": "batch run"})
        assert args == ["describe", "batch", "run"]

    def test_list_positional_accepts_actual_list(self):
        payload = _describe_payload({
            "path": ["describe"],
            "summary": "",
            "options": [],
            "positionals": [
                {"name": "command_path", "type": "list[str]", "required": False},
            ],
            "effects": ["read"],
            "supported_formats": ["json"],
        })
        t = generate_tools(payload)[0]
        args = t.build_argv({"command_path": ["batch", "run"]})
        assert args == ["describe", "batch", "run"]

    def test_positional_required_validated(self):
        """build_argv raises if a required positional is missing — better
        to fail in the wrapper than send malformed argv to bcli."""
        payload = _describe_payload({
            "path": ["post"],
            "summary": "",
            "options": [{"name": "--data", "type": "str"}],
            "positionals": [
                {"name": "endpoint", "type": "str", "required": True},
            ],
            "effects": ["mutating"],
            "supported_formats": ["json"],
            "emits_result_envelope": True,
        })
        t = generate_tools(payload)[0]
        with pytest.raises(ValueError, match=r"endpoint"):
            t.build_argv({"data": "{}"})


# ── Edge case: no positionals field on the describe entry ────────────


class TestBackwardsCompatibility:
    def test_describe_without_positionals_field_is_tolerated(self):
        """An older describe output (pre-Phase 5) doesn't carry
        ``positionals``. Treat missing as an empty list so old caches
        and older bcli versions still produce a usable tool list."""
        payload = _describe_payload({
            "path": ["get"],
            "summary": "GET records.",
            "options": [{"name": "--top", "type": "int"}],
            # no positionals key
            "effects": ["read"],
            "supported_formats": ["json"],
        })
        tools = generate_tools(payload)
        assert len(tools) == 1
        assert tools[0].input_schema["required"] == []
