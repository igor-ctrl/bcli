"""Tests for the bcli-mcp tool callables.

We invoke each ``@mcp.tool()`` function directly with the runner mocked.
Tests assert: argv shape passed to bcli, top-cap enforcement on ``query``,
single-record wrap behaviour, and profile passthrough.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bcli_mcp import _server


# ── query ─────────────────────────────────────────────────────────────────


class TestQueryTool:
    @pytest.mark.asyncio
    async def test_default_top_is_50(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.query(entity="customers")
        args = run.call_args.args
        assert "--top" in args
        assert args[args.index("--top") + 1] == "50"

    @pytest.mark.asyncio
    async def test_explicit_top_passes_through(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.query(entity="customers", top=200)
        args = run.call_args.args
        assert args[args.index("--top") + 1] == "200"

    @pytest.mark.asyncio
    async def test_top_clamps_at_max(self):
        """An agent asking for 50000 records gets clamped — keeps responses
        bounded so the model can't accidentally swamp its own context."""
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.query(entity="customers", top=50_000)
        args = run.call_args.args
        assert args[args.index("--top") + 1] == "1000"

    @pytest.mark.asyncio
    async def test_top_floor_is_1(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.query(entity="customers", top=0)
        args = run.call_args.args
        assert args[args.index("--top") + 1] == "1"

    @pytest.mark.asyncio
    async def test_filter_select_orderby_passed_through(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.query(
                entity="customers",
                filter="displayName eq 'X'",
                select="number,displayName",
                orderby="number desc",
            )
        args = run.call_args.args
        assert "--filter" in args
        assert args[args.index("--filter") + 1] == "displayName eq 'X'"
        assert args[args.index("--select") + 1] == "number,displayName"
        assert args[args.index("--orderby") + 1] == "number desc"

    @pytest.mark.asyncio
    async def test_record_id_appended_after_entity(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value={"id": "c-1"}) as run:
            result = await _server.query(entity="customers", record_id="c-1")
        args = run.call_args.args
        assert args[:3] == ("get", "customers", "c-1")
        # Single-record dict gets wrapped to keep return type stable
        assert result == [{"id": "c-1"}]

    @pytest.mark.asyncio
    async def test_publisher_group_version_passed_through(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.query(
                entity="customEntity",
                publisher="acme", group="finance", version="v1.5",
            )
        args = run.call_args.args
        assert args[args.index("--publisher") + 1] == "acme"
        assert args[args.index("--group") + 1] == "finance"
        assert args[args.index("--version") + 1] == "v1.5"

    @pytest.mark.asyncio
    async def test_profile_passes_to_runner(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.query(entity="customers", profile="sandbox")
        assert run.call_args.kwargs["profile"] == "sandbox"


# ── list_endpoints ────────────────────────────────────────────────────────


class TestListEndpointsTool:
    @pytest.mark.asyncio
    async def test_basic_call(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.list_endpoints()
        assert run.call_args.args == ("endpoint", "list")

    @pytest.mark.asyncio
    async def test_filters_passed_through(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.list_endpoints(
                category="finance", custom_only=True,
            )
        args = run.call_args.args
        assert "--custom" in args
        assert args[args.index("--category") + 1] == "finance"

    @pytest.mark.asyncio
    async def test_standard_only_flag(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.list_endpoints(standard_only=True)
        assert "--standard" in run.call_args.args


# ── describe_endpoint ─────────────────────────────────────────────────────


class TestDescribeEndpointTool:
    @pytest.mark.asyncio
    async def test_calls_endpoint_info(self):
        with patch(
            "bcli_mcp._server.run_bcli_json",
            return_value={"name": "customers"},
        ) as run:
            result = await _server.describe_endpoint(name="customers")
        assert run.call_args.args == ("endpoint", "info", "customers")
        assert result == {"name": "customers"}

    @pytest.mark.asyncio
    async def test_discover_fields_default_false_skips_side_effect(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value={}) as info, \
             patch("bcli_mcp._server.run_bcli_side_effect") as side:
            await _server.describe_endpoint(name="customers")
        side.assert_not_called()
        info.assert_called_once()

    @pytest.mark.asyncio
    async def test_discover_fields_true_runs_fields_then_info(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value={}) as info, \
             patch("bcli_mcp._server.run_bcli_side_effect") as side:
            await _server.describe_endpoint(name="customers", discover_fields=True)
        side.assert_called_once_with(
            "endpoint", "fields", "customers", profile=None,
        )
        info.assert_called_once_with(
            "endpoint", "info", "customers", profile=None,
        )

    @pytest.mark.asyncio
    async def test_discover_fields_swallows_side_effect_failure(self):
        """If discovery fails (entity needs filter, no records, etc.) we
        still return the info payload — fields_discovered will be False
        and the agent can fall back to a probe query."""
        from mcp.server.fastmcp.exceptions import ToolError

        with patch(
            "bcli_mcp._server.run_bcli_json",
            return_value={"name": "customerSales", "fields_discovered": False},
        ) as info, patch(
            "bcli_mcp._server.run_bcli_side_effect",
            side_effect=ToolError("BC returned 400"),
        ):
            result = await _server.describe_endpoint(
                name="customerSales", discover_fields=True,
            )
        assert result == {"name": "customerSales", "fields_discovered": False}
        info.assert_called_once()

    @pytest.mark.asyncio
    async def test_discover_fields_passes_profile_through(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value={}), \
             patch("bcli_mcp._server.run_bcli_side_effect") as side:
            await _server.describe_endpoint(
                name="customers", discover_fields=True, profile="sandbox",
            )
        assert side.call_args.kwargs["profile"] == "sandbox"


# ── list_companies ────────────────────────────────────────────────────────


class TestListCompaniesTool:
    @pytest.mark.asyncio
    async def test_calls_company_list(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.list_companies()
        assert run.call_args.args == ("company", "list")

    @pytest.mark.asyncio
    async def test_profile_propagates(self):
        with patch("bcli_mcp._server.run_bcli_json", return_value=[]) as run:
            await _server.list_companies(profile="sandbox")
        assert run.call_args.kwargs["profile"] == "sandbox"


# ── Tool registration sanity ──────────────────────────────────────────────


class TestServerRegistration:
    def test_four_tools_exposed(self):
        """The tool surface is deliberately small — the design contract."""
        registered = set(_server.mcp._tool_manager._tools.keys())
        assert registered == {
            "query",
            "list_endpoints",
            "describe_endpoint",
            "list_companies",
        }
