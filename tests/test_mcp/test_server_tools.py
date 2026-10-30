"""Tests for the bcli-mcp server after the Phase 5 rewrite.

The server now:

1. Subprocesses ``bcli describe --format json`` once on startup.
2. Builds the tool list from the describe output via
   ``_tool_generator.generate_tools``.
3. Registers each generated tool with FastMCP. Read tools shell out via
   ``run_bcli_json`` and return parsed stdout. Record writes
   (``bcli_post`` / ``bcli_patch`` / ``bcli_delete``) run in-process
   through the SDK write operation. Other mutating tools (attach upload,
   batch run) pass ``--result-out <tmpfile>``, subprocess the CLI, and
   return the envelope content as the tool result.

These tests mock the describe subprocess + the bcli subprocesses, and
drive in-process writes against ``fake_bc``, so no real bcli or BC is hit.
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
            "path": ["attach", "upload"],
            "summary": "Attach a file to an existing record.",
            "options": [
                {"name": "--file", "type": "str"},
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
def mcp_config(tmp_path, monkeypatch):
    """A hermetic config: ``dev`` (Sandbox), ``prod`` (Production) and a
    read-only ``locked`` profile. Never the developer's real config."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[defaults]\nprofile = "dev"\n\n'
        '[profiles.dev]\ntenant_id = "t1"\nenvironment = "Sandbox"\ncompany_id = "c-dev"\n\n'
        '[profiles.prod]\ntenant_id = "t1"\nenvironment = "Production"\ncompany_id = "c-prod"\n\n'
        '[profiles.locked]\ntenant_id = "t1"\nenvironment = "Sandbox"\ncompany_id = "c-dev"\n'
        "disable_writes = true\n"
    )
    monkeypatch.setattr("bcli.config._loader.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._loader._find_project_config", lambda: None)
    import bcli.registry._registry as registry_mod

    monkeypatch.setattr(registry_mod, "REGISTRIES_DIR", tmp_path / "registries")


@pytest.fixture
def no_subprocess(monkeypatch):
    """In-process writes must never shell out."""
    def boom(*a, **kw):
        raise AssertionError("record write shelled out to bcli")

    monkeypatch.setattr("subprocess.run", boom)
    monkeypatch.setattr("bcli_mcp._server.run_bcli_with_envelope", boom)
    monkeypatch.setattr("bcli_mcp._server.run_bcli_json", boom)


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
        assert registered == {"bcli_get", "bcli_post", "bcli_attach_upload", "bcli_company_list"}

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


# ── Tool invocation: record writes (in-process SDK) ───────────────────


class TestSdkWriteInvocation:
    @pytest.mark.asyncio
    async def test_post_runs_in_process_and_returns_correlation_id(
        self, fresh_server, mcp_config, fake_bc, no_subprocess,
    ):
        fake_bc.reply({"id": "vnd-9", "number": "V1"})
        fake_bc.status = 201
        fake_bc.correlation_id = "corr-abc"

        tool = fresh_server._tool_manager._tools["bcli_post"]
        result = await tool.fn(endpoint="vendors", data='{"x":1}', yes=True)

        assert [(s.method, s.json_body) for s in fake_bc.sent] == [("POST", {"x": 1})]
        assert result["bc_correlation_id"] == "corr-abc"
        assert result["status"] == "succeeded"
        assert result["exit_code"] == 0
        assert result["method"] == "POST"
        assert result["endpoint"] == "vendors"
        assert result["resolved_url"] == fake_bc.last.url
        assert result["record_id"] == "vnd-9"
        assert result["profile"] == "dev"
        assert result["environment"] == "Sandbox"
        assert result["company"] == "c-dev"
        assert result["body"] == {"id": "vnd-9", "number": "V1"}

    @pytest.mark.asyncio
    async def test_failed_write_raises_toolerror_with_correlation_id(
        self, fresh_server, mcp_config, fake_bc, no_subprocess,
    ):
        """A BC error surfaces as an MCP ``ToolError`` quoting the
        correlation id so the agent can cite it in a support ticket."""
        from mcp.server.fastmcp.exceptions import ToolError

        from bcli.errors import ValidationError

        fake_bc.reply(ValidationError("bad field", status_code=400, correlation_id="corr-xyz"))
        tool = fresh_server._tool_manager._tools["bcli_post"]
        with pytest.raises(ToolError) as excinfo:
            await tool.fn(endpoint="vendors", data='{"x":1}', yes=True)
        assert "corr-xyz" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_profile_selects_write_target(
        self, fresh_server, mcp_config, fake_bc, no_subprocess,
    ):
        tool = fresh_server._tool_manager._tools["bcli_post"]
        result = await tool.fn(endpoint="vendors", data="{}", yes=True, profile="prod")
        assert "/Production/" in fake_bc.last.url
        assert "c-prod" in fake_bc.last.url
        assert result["profile"] == "prod"
        assert result["environment"] == "Production"

    @pytest.mark.asyncio
    async def test_disable_writes_profile_requires_yes(
        self, fresh_server, mcp_config, fake_bc, no_subprocess,
    ):
        from mcp.server.fastmcp.exceptions import ToolError

        tool = fresh_server._tool_manager._tools["bcli_post"]
        with pytest.raises(ToolError, match="disable_writes"):
            await tool.fn(endpoint="vendors", data="{}", profile="locked")
        assert fake_bc.sent == []

        await tool.fn(endpoint="vendors", data="{}", profile="locked", yes=True)
        assert [s.method for s in fake_bc.sent] == ["POST"]


# ── Tool invocation: other mutating tools (subprocess + envelope) ─────


def _envelope(**overrides):
    env = {
        "version": "0.1", "invocation_id": "inv-1", "tool_version": "0.4.0",
        "profile": "dev", "environment": "Sandbox", "company": "c-1",
        "method": "UPLOAD", "endpoint": "salesInvoices",
        "resolved_url": "https://x/salesInvoices", "record_id": "si-9",
        "dry_run": False, "status": "succeeded", "exit_code": 0,
        "bc_correlation_id": None, "telemetry_event_id": None,
        "audit_log_offset": None, "started_at": "2026-05-17T00:00:00Z",
        "duration_ms": 42,
    }
    env.update(overrides)
    return env


class TestMutatingToolInvocation:
    @pytest.mark.asyncio
    async def test_mutating_tool_passes_result_out_and_reads_envelope(
        self, fresh_server, tmp_path,
    ):
        """The server must pass ``--result-out <tmp>`` and return the
        envelope content. Stdout is dropped (envelope has everything)."""
        envelope = _envelope()

        def _fake_run(argv, env, capture_envelope_path):
            # Write the envelope where the runner expects it.
            Path(capture_envelope_path).write_text(json.dumps(envelope))
            return 0, "", ""

        tool = fresh_server._tool_manager._tools["bcli_attach_upload"]
        with patch("bcli_mcp._server.run_bcli_with_envelope", side_effect=_fake_run):
            result = await tool.fn(endpoint="salesInvoices", file="a.pdf", yes=True)
        assert result == envelope

    @pytest.mark.asyncio
    async def test_failed_envelope_raises_toolerror(self, fresh_server, tmp_path):
        """When the envelope's ``status="failed"``, the tool surfaces an
        MCP error with the envelope as the body so the agent can read
        ``exit_code``, ``bc_correlation_id``, etc."""
        from mcp.server.fastmcp.exceptions import ToolError

        failed = _envelope(
            invocation_id="inv-2", resolved_url=None, record_id=None,
            status="failed", exit_code=6, bc_correlation_id="corr-xyz",
        )

        def _fake_run(argv, env, capture_envelope_path):
            Path(capture_envelope_path).write_text(json.dumps(failed))
            return 6, "", ""

        tool = fresh_server._tool_manager._tools["bcli_attach_upload"]
        with patch("bcli_mcp._server.run_bcli_with_envelope", side_effect=_fake_run):
            with pytest.raises(ToolError) as excinfo:
                await tool.fn(endpoint="salesInvoices", file="a.pdf", yes=True)
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
        captured_argv = {}

        def _fake_run(argv, env, capture_envelope_path):
            captured_argv["v"] = argv
            Path(capture_envelope_path).write_text(json.dumps(_envelope()))
            return 0, "", ""

        tool = fresh_server._tool_manager._tools["bcli_attach_upload"]
        with patch("bcli_mcp._server.run_bcli_with_envelope", side_effect=_fake_run):
            await tool.fn(endpoint="salesInvoices", file="a.pdf", yes=True)
        argv = captured_argv["v"]
        # Positional first, options after.
        assert argv[:3] == ["attach", "upload", "salesInvoices"]
        assert "--file" in argv
        assert argv[argv.index("--file") + 1] == "a.pdf"
        # The server passes a clean argv — runner adds --result-out / --format.
        assert "--result-out" not in argv
        assert "--format" not in argv


# ── Profile passthrough on subprocess mutating tools ──────────────────


class TestMutatingProfilePassthrough:
    @pytest.mark.asyncio
    async def test_profile_propagates_to_subprocess(self, fresh_server, tmp_path):
        captured = {}

        def _fake_run(argv, env, capture_envelope_path):
            captured["argv"] = argv
            captured["env"] = env
            Path(capture_envelope_path).write_text(json.dumps(_envelope(profile="prod")))
            return 0, "", ""

        tool = fresh_server._tool_manager._tools["bcli_attach_upload"]
        with patch("bcli_mcp._server.run_bcli_with_envelope", side_effect=_fake_run):
            await tool.fn(endpoint="v", file="a.pdf", yes=True, profile="prod")
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
