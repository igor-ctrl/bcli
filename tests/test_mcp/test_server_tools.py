"""Tests for the bcli-mcp server after the Phase 5 rewrite.

The server now:

1. Subprocesses ``bcli describe --format json`` once on startup.
2. Builds the tool list from the describe output via
   ``_tool_generator.generate_tools``.
3. Registers each generated tool with FastMCP. Read tools shell out via
   ``run_bcli_json`` and return parsed stdout. Mutating tools pass
   ``--result-out <tmpfile>``, subprocess the CLI, and return the
   envelope content as the tool result.

These tests mock the describe subprocess + the bcli subprocesses so no
real bcli is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bcli_mcp import _server


# ── Fixtures ──────────────────────────────────────────────────────────


_DESCRIBE_FIXTURE = {
    "version": "0.1",
    "tool": "bcli",
    "tool_version": "0.4.0",
    "profile": "dev",
    "commands": [
        {
            "path": ["get"],
            "summary": "GET records from a Business Central entity.",
            "options": [
                {"name": "--filter", "type": "str", "validates": "odata-filter"},
                {"name": "--top", "type": "int",
                 "limits": {"default": 50, "minimum": 1, "maximum": 1000}},
                {"name": "--format", "type": "str"},
            ],
            "positionals": [
                {"name": "endpoint", "type": "str", "required": True},
                {"name": "record_id", "type": "str", "required": False},
            ],
            "effects": ["read"],
            "supported_formats": ["table", "json"],
        },
        {
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
        },
        {
            "path": ["company", "list"],
            "summary": "List companies on the active environment.",
            "options": [],
            "positionals": [],
            "effects": ["read"],
            "supported_formats": ["json"],
        },
    ],
    "registry": {"tier_1_custom_count": 0, "tier_2_standard_enabled": True, "endpoints": []},
    "profile_constraints": {
        "disable_writes": False, "disable_standard_api": False, "allowed_categories": None,
    },
}


@pytest.fixture
def fresh_server():
    """Force the server module to rebuild its tool list from a fixture
    describe payload. Yields the rebuilt FastMCP instance."""
    instance = _server._build_server(describe_payload=_DESCRIBE_FIXTURE)
    yield instance


# ── Tool list mirrors describe ────────────────────────────────────────


class TestToolListMirrorsDescribe:
    def test_one_tool_per_command_in_describe(self, fresh_server):
        """Every command entry → one MCP tool. No extras, no missing."""
        registered = set(fresh_server._tool_manager._tools.keys())
        assert registered == {"bcli_get", "bcli_post", "bcli_company_list"}

    def test_other_effects_excluded(self):
        """``effects: ["other"]`` (e.g. ``auth login``, ``config init``)
        is filtered out — interactive commands aren't safely scriptable."""
        payload = dict(_DESCRIBE_FIXTURE)
        payload["commands"] = list(_DESCRIBE_FIXTURE["commands"]) + [
            {"path": ["auth", "login"], "summary": "", "options": [],
             "positionals": [], "effects": ["other"], "supported_formats": ["json"]},
        ]
        server = _server._build_server(describe_payload=payload)
        assert "bcli_auth_login" not in server._tool_manager._tools


# ── Tool invocation: read path ────────────────────────────────────────


class TestReadToolInvocation:
    @pytest.mark.asyncio
    async def test_read_tool_subprocesses_with_format_json(self, fresh_server):
        """A read tool call → ``bcli get customers --filter ... --format json``."""
        tool = fresh_server._tool_manager._tools["bcli_get"]
        with patch(
            "bcli_mcp._server.run_bcli_json",
            return_value=[{"id": "c-1"}],
        ) as run:
            result = await tool.fn(endpoint="customers", filter="x eq 1")
        args = run.call_args.args
        # Positional first, then option flags.
        assert args[0:2] == ("get", "customers")
        assert "--filter" in args
        assert args[args.index("--filter") + 1] == "x eq 1"
        assert result == [{"id": "c-1"}]

    @pytest.mark.asyncio
    async def test_read_tool_with_only_required_positional(self, fresh_server):
        tool = fresh_server._tool_manager._tools["bcli_company_list"]
        with patch(
            "bcli_mcp._server.run_bcli_json",
            return_value=[{"id": "BTUSALLC"}],
        ) as run:
            result = await tool.fn()
        assert run.call_args.args == ("company", "list")
        assert result == [{"id": "BTUSALLC"}]

    @pytest.mark.asyncio
    async def test_profile_kwarg_passes_through(self, fresh_server):
        tool = fresh_server._tool_manager._tools["bcli_get"]
        with patch(
            "bcli_mcp._server.run_bcli_json", return_value=[],
        ) as run:
            await tool.fn(endpoint="customers", profile="prod")
        assert run.call_args.kwargs["profile"] == "prod"


# ── Tool invocation: mutating path ────────────────────────────────────


class TestMutatingToolInvocation:
    @pytest.mark.asyncio
    async def test_mutating_tool_passes_result_out_and_reads_envelope(
        self, fresh_server, tmp_path,
    ):
        """The server must pass ``--result-out <tmp>`` and return the
        envelope content. Stdout is dropped (envelope has everything)."""
        envelope = {
            "version": "0.1", "invocation_id": "inv-1", "tool_version": "0.4.0",
            "profile": "dev", "environment": "Sandbox", "company": "c-1",
            "method": "POST", "endpoint": "vendors",
            "resolved_url": "https://x/vendors", "record_id": "vnd-9",
            "dry_run": False, "status": "succeeded", "exit_code": 0,
            "bc_correlation_id": None, "telemetry_event_id": None,
            "audit_log_offset": None, "started_at": "2026-05-17T00:00:00Z",
            "duration_ms": 42,
        }

        def _fake_run(argv, env, capture_envelope_path):
            # Write the envelope where the runner expects it.
            Path(capture_envelope_path).write_text(json.dumps(envelope))
            return 0, "", ""

        tool = fresh_server._tool_manager._tools["bcli_post"]
        with patch("bcli_mcp._server.run_bcli_with_envelope", side_effect=_fake_run):
            result = await tool.fn(endpoint="vendors", data='{"x":1}', yes=True)
        assert result == envelope

    @pytest.mark.asyncio
    async def test_failed_envelope_raises_toolerror(self, fresh_server, tmp_path):
        """When the envelope's ``status="failed"``, the tool surfaces an
        MCP error with the envelope as the body so the agent can read
        ``exit_code``, ``bc_correlation_id``, etc."""
        from mcp.server.fastmcp.exceptions import ToolError

        failed = {
            "version": "0.1", "invocation_id": "inv-2", "tool_version": "0.4.0",
            "profile": "dev", "environment": "Sandbox", "company": "c-1",
            "method": "POST", "endpoint": "vendors", "resolved_url": None,
            "record_id": None, "dry_run": False, "status": "failed",
            "exit_code": 6, "bc_correlation_id": "corr-xyz",
            "telemetry_event_id": None, "audit_log_offset": None,
            "started_at": "2026-05-17T00:00:00Z", "duration_ms": 11,
        }

        def _fake_run(argv, env, capture_envelope_path):
            Path(capture_envelope_path).write_text(json.dumps(failed))
            return 6, "", ""

        tool = fresh_server._tool_manager._tools["bcli_post"]
        with patch("bcli_mcp._server.run_bcli_with_envelope", side_effect=_fake_run):
            with pytest.raises(ToolError) as excinfo:
                await tool.fn(endpoint="vendors", data='{"x":1}', yes=True)
        # The exception message includes the correlation id so the agent
        # can quote it back when raising a support ticket.
        assert "corr-xyz" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_mutating_tool_passes_clean_argv_to_runner(
        self, fresh_server, tmp_path,
    ):
        """The server hands the runner a CLEAN argv (positionals + flags
        only). ``--result-out`` and ``--format json`` are appended inside
        :func:`run_bcli_with_envelope` so the boundary is single-sourced.

        The runner-side contract is exercised in ``test_runner.py``;
        here we just pin the server side: it shouldn't be re-adding
        either flag itself."""
        envelope = {
            "version": "0.1", "invocation_id": "x", "tool_version": "0.4.0",
            "profile": "dev", "environment": "Sandbox", "company": "c-1",
            "method": "POST", "endpoint": "vendors", "resolved_url": None,
            "record_id": None, "dry_run": False, "status": "succeeded",
            "exit_code": 0, "bc_correlation_id": None,
            "telemetry_event_id": None, "audit_log_offset": None,
            "started_at": "x", "duration_ms": 0,
        }
        captured_argv = {}

        def _fake_run(argv, env, capture_envelope_path):
            captured_argv["v"] = argv
            Path(capture_envelope_path).write_text(json.dumps(envelope))
            return 0, "", ""

        tool = fresh_server._tool_manager._tools["bcli_post"]
        with patch("bcli_mcp._server.run_bcli_with_envelope", side_effect=_fake_run):
            await tool.fn(endpoint="vendors", data="{}", yes=True)
        argv = captured_argv["v"]
        # Positional first, options after.
        assert argv[:2] == ["post", "vendors"]
        assert "--data" in argv
        assert argv[argv.index("--data") + 1] == "{}"
        # The server passes a clean argv — runner adds --result-out / --format.
        assert "--result-out" not in argv
        assert "--format" not in argv


# ── Profile passthrough on mutating tools ─────────────────────────────


class TestMutatingProfilePassthrough:
    @pytest.mark.asyncio
    async def test_profile_propagates_to_subprocess(self, fresh_server, tmp_path):
        envelope = {
            "version": "0.1", "invocation_id": "x", "tool_version": "0.4.0",
            "profile": "prod", "environment": "Production", "company": "c-1",
            "method": "POST", "endpoint": "v", "resolved_url": None,
            "record_id": None, "dry_run": False, "status": "succeeded",
            "exit_code": 0, "bc_correlation_id": None,
            "telemetry_event_id": None, "audit_log_offset": None,
            "started_at": "x", "duration_ms": 0,
        }
        captured = {}

        def _fake_run(argv, env, capture_envelope_path):
            captured["argv"] = argv
            captured["env"] = env
            Path(capture_envelope_path).write_text(json.dumps(envelope))
            return 0, "", ""

        tool = fresh_server._tool_manager._tools["bcli_post"]
        with patch("bcli_mcp._server.run_bcli_with_envelope", side_effect=_fake_run):
            await tool.fn(endpoint="v", data="{}", yes=True, profile="prod")
        assert "--profile" in captured["argv"]
        assert captured["argv"][captured["argv"].index("--profile") + 1] == "prod"
        assert captured["env"]["BCLI_PROFILE"] == "prod"


# ── Describe failure fallback ─────────────────────────────────────────


class TestDescribeFailureFallback:
    def test_describe_failure_yields_empty_tool_list(self):
        """If ``bcli describe`` exits non-zero or doesn't exist, the
        server doesn't crash on startup — it builds with zero tools and
        logs the error. Caller can re-invoke after fixing the install."""
        with patch(
            "bcli_mcp._server._load_describe_payload",
            side_effect=RuntimeError("bcli not found"),
        ):
            server = _server._build_server()  # no payload kwarg
        # No tools registered, but the server itself is built.
        assert server._tool_manager._tools == {}

    def test_describe_returns_no_commands_yields_empty_tool_list(self):
        """An empty ``commands: []`` (broken install? unconfigured profile?)
        is treated the same — start clean, register nothing."""
        empty = {
            "version": "0.1", "tool": "bcli", "tool_version": "0.4.0",
            "profile": None, "commands": [],
            "registry": {"tier_1_custom_count": 0, "tier_2_standard_enabled": True,
                         "endpoints": []},
            "profile_constraints": {"disable_writes": None,
                                    "disable_standard_api": None,
                                    "allowed_categories": None},
        }
        server = _server._build_server(describe_payload=empty)
        assert server._tool_manager._tools == {}


# ── Tools generated only once on startup ──────────────────────────────


class TestStartupCache:
    def test_describe_invoked_once_per_build(self):
        """``_build_server`` calls describe at most once; later tool
        calls re-use the cached payload + generated tools."""
        with patch(
            "bcli_mcp._server._load_describe_payload",
            return_value=_DESCRIBE_FIXTURE,
        ) as load:
            _server._build_server()
        assert load.call_count == 1
