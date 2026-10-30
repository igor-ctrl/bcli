"""Tests for the ``bcli post`` verb, focused on ``--data``/``-d`` handling.

``post`` was one of two commands whose bare ``json.loads()`` call on
``--data`` surfaced a raw traceback on malformed input; see
``bcli_cli._data_arg`` for the fix and ``test_data_arg.py`` for the full
heuristic matrix. These tests confirm the command wires the shared
helper in correctly — bad ``--data`` must fail before any network call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.commands import post_cmd


@pytest.fixture
def cli_state():
    cfg = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={
            "dev": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                company_id="c-123",
                disable_writes=False,
            ),
        },
    )
    state._config = cfg
    state._registry = None
    state.profile_name = None
    state.env_override = None
    state.company_override = None
    state.format = "table"
    state.dry_run = False
    state.quiet = True
    yield state
    state._config = None
    state._registry = None
    state.profile_name = None
    state.dry_run = False


@pytest.fixture
def bc(fake_bc):
    """The real client against the fake transport; BC answers ``{"result": "ok"}``."""
    fake_bc.default = {"result": "ok"}
    return fake_bc


@pytest.fixture(autouse=True)
def non_interactive(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


def _run(*, endpoint="vendors", data='{"displayName": "Acme"}', yes=True, **kwargs):
    kwargs.setdefault("format", None)
    kwargs.setdefault("publisher", None)
    kwargs.setdefault("group", None)
    kwargs.setdefault("version", None)
    kwargs.setdefault("result_out", None)
    kwargs.setdefault("result_fd", None)
    kwargs.setdefault("idempotency_key", None)
    return post_cmd.post_command(endpoint=endpoint, data=data, yes=yes, **kwargs)


class TestValidData:
    def test_inline_json_literal_parsed(self, cli_state, bc):
        _run(data='{"a": 1, "b": "two"}')
        assert bc.last.method == "POST"
        assert bc.last.url.endswith("/companies(c-123)/vendors")
        assert bc.last.json_body == {"a": 1, "b": "two"}

    def test_data_from_file(self, cli_state, bc, tmp_path: Path):
        f = tmp_path / "payload.json"
        f.write_text('{"loaded": "from-file"}', encoding="utf-8")
        _run(data=f"@{f}")
        body_arg = bc.last.json_body
        assert body_arg == {"loaded": "from-file"}


class TestMalformedData:
    def test_malformed_inline_json_raises_bad_parameter(self, cli_state, bc):
        with pytest.raises(typer.BadParameter):
            _run(data="{not valid json")
        # Must fail before any network call goes out.
        assert bc.sent == []

    def test_bare_file_path_without_at_prefix_hints_the_fix(
        self, cli_state, bc, tmp_path: Path,
    ):
        f = tmp_path / "payload.json"
        f.write_text('{"x": 1}', encoding="utf-8")
        with pytest.raises(typer.BadParameter) as exc_info:
            _run(data=str(f))
        assert "looks like a file path" in str(exc_info.value)
        assert f"-d @{f}" in str(exc_info.value)
        assert bc.sent == []

    def test_missing_at_file_keeps_file_not_found(self, cli_state, bc):
        with pytest.raises(typer.BadParameter) as exc_info:
            _run(data="@/no/such/file.json")
        assert "File not found" in str(exc_info.value)
        assert bc.sent == []

    def test_malformed_at_file_names_the_file(
        self, cli_state, bc, tmp_path: Path,
    ):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(typer.BadParameter) as exc_info:
            _run(data=f"@{f}")
        assert str(f) in str(exc_info.value)
        assert bc.sent == []


class TestEnvelope:
    def test_envelope_written_on_success(self, cli_state, bc, tmp_path: Path):
        out = tmp_path / "env.json"
        _run(result_out=out)
        env = json.loads(out.read_text())
        assert env["status"] == "succeeded"
        assert env["method"] == "POST"
        assert env["endpoint"] == "vendors"

    def test_envelope_carries_bc_correlation_id(self, cli_state, bc, tmp_path: Path):
        out = tmp_path / "env.json"
        _run(result_out=out)
        assert json.loads(out.read_text())["bc_correlation_id"] == "corr-test"
