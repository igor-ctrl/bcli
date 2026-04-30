"""Tests for the bcli-mcp subprocess wrapper.

The wrapper translates a Python tool call into ``bcli ... --format json``,
parses stdout, and surfaces non-zero exits as ``ToolError`` with Rich
markup stripped. These tests mock ``subprocess.run`` so no real bcli
process is spawned.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from bcli_mcp._runner import _strip_rich, run_bcli_json, run_bcli_side_effect
from mcp.server.fastmcp.exceptions import ToolError


def _mock_completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["bcli"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# ── _strip_rich ──────────────────────────────────────────────────────────


class TestStripRich:
    def test_removes_simple_color_tags(self):
        assert _strip_rich("[red]Error[/red]") == "Error"

    def test_removes_nested_styles(self):
        assert _strip_rich("[bold][cyan]name[/cyan][/bold]") == "name"

    def test_removes_dim_with_attribute(self):
        assert _strip_rich("[dim]42 records[/dim]") == "42 records"

    def test_passes_through_plain_text(self):
        assert _strip_rich("plain text") == "plain text"

    def test_strips_leading_trailing_whitespace(self):
        assert _strip_rich("  [red]oops[/red]\n") == "oops"

    def test_handles_unmatched_tags(self):
        # Unbalanced tags still get scrubbed (best-effort)
        assert _strip_rich("[red]oops") == "oops"


# ── run_bcli_json — happy path ───────────────────────────────────────────


class TestRunnerHappyPath:
    def test_parses_json_array(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout='[{"id": "c-1"}]')
            result = run_bcli_json("company", "list")
        assert result == [{"id": "c-1"}]

    def test_parses_json_object(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout='{"name": "customers"}')
            result = run_bcli_json("endpoint", "info", "customers")
        assert result == {"name": "customers"}

    def test_appends_format_json_flag(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout="[]")
            run_bcli_json("get", "customers")
        argv = run.call_args.args[0]
        assert argv[-2:] == ["--format", "json"]
        assert argv[0] == "bcli"

    def test_empty_stdout_returns_empty_list(self):
        """Some CLI commands print nothing to stdout when there's no data —
        treat that as an empty result, not a parse failure."""
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout="", stderr="")
            assert run_bcli_json("get", "customers") == []


# ── Profile passthrough ──────────────────────────────────────────────────


class TestRunnerProfile:
    def test_profile_overrides_argv_and_env(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout="[]")
            run_bcli_json("get", "customers", profile="prod")
        argv = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        # --profile lands before the subcommand
        assert "--profile" in argv and argv[argv.index("--profile") + 1] == "prod"
        # And BCLI_PROFILE env var is set so any nested process honours it too
        assert env["BCLI_PROFILE"] == "prod"

    def test_no_profile_omits_flag(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout="[]")
            run_bcli_json("get", "customers")
        argv = run.call_args.args[0]
        assert "--profile" not in argv


# ── Error paths ──────────────────────────────────────────────────────────


class TestRunnerErrors:
    def test_non_zero_exit_raises_toolerror_with_stripped_markup(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(
                1, stdout="", stderr="[red]Auth failed:[/red] [bold]401[/bold]",
            )
            with pytest.raises(ToolError, match=r"exited 1.*Auth failed.*401"):
                run_bcli_json("get", "customers")

    def test_falls_back_to_stdout_when_stderr_empty(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(
                2, stdout="usage: bcli get …", stderr="",
            )
            with pytest.raises(ToolError, match=r"usage: bcli get"):
                run_bcli_json("get")

    def test_malformed_json_raises_toolerror(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout="<html>500</html>")
            with pytest.raises(ToolError, match=r"non-JSON output"):
                run_bcli_json("get", "customers")

    def test_missing_bcli_binary_raises_toolerror(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("bcli")):
            with pytest.raises(ToolError, match=r"bcli executable not found"):
                run_bcli_json("get", "customers")

    def test_timeout_raises_toolerror(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["bcli"], 1)):
            with pytest.raises(ToolError, match=r"timed out"):
                run_bcli_json("get", "customers", timeout=1)


# ── run_bcli_side_effect ─────────────────────────────────────────────────


class TestSideEffectRunner:
    def test_does_not_append_format_json(self):
        """Side-effect commands have non-JSON human stdout — adding
        --format json to commands like ``endpoint fields`` would either
        be a no-op or change behaviour. Make sure we DON'T append it."""
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout="Fields for…")
            run_bcli_side_effect("endpoint", "fields", "customers")
        argv = run.call_args.args[0]
        assert "--format" not in argv

    def test_returns_none_on_success(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout="any text")
            assert run_bcli_side_effect("endpoint", "fields", "customers") is None

    def test_ignores_stdout_content(self):
        """Non-JSON, garbage, empty — all fine as long as exit is 0."""
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout="<html>")
            run_bcli_side_effect("endpoint", "fields", "customers")  # no raise

    def test_non_zero_exit_raises_toolerror(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(
                1, stdout="", stderr="[red]Endpoint not found[/red]",
            )
            with pytest.raises(ToolError, match=r"Endpoint not found"):
                run_bcli_side_effect("endpoint", "fields", "nope")

    def test_profile_passes_through(self):
        with patch("subprocess.run") as run:
            run.return_value = _mock_completed(0, stdout="")
            run_bcli_side_effect("endpoint", "fields", "customers", profile="prod")
        argv = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        assert "--profile" in argv and argv[argv.index("--profile") + 1] == "prod"
        assert env["BCLI_PROFILE"] == "prod"
