"""Tests for the ``bcli patch`` verb, focused on ``--data``/``-d`` handling.

``patch`` was the other command whose bare ``json.loads()`` call on
``--data`` surfaced a raw traceback on malformed input; see
``bcli_cli._data_arg`` for the fix and ``test_data_arg.py`` for the full
heuristic matrix. These tests confirm the command wires the shared
helper in correctly — bad ``--data`` must fail before any network call.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import typer

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.commands import patch_cmd


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
def fake_client(monkeypatch):
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.patch = AsyncMock(return_value={"result": "ok"})
    c._resolve_url = lambda entity, **kw: f"https://example.test/{entity}"
    monkeypatch.setattr(state, "make_async_client", lambda **_: c)
    return c


@pytest.fixture(autouse=True)
def non_interactive(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


def _run(
    *,
    endpoint="vendors",
    record_id="vnd-1",
    data='{"displayName": "Renamed"}',
    etag="*",
    yes=True,
    **kwargs,
):
    kwargs.setdefault("format", None)
    kwargs.setdefault("publisher", None)
    kwargs.setdefault("group", None)
    kwargs.setdefault("version", None)
    kwargs.setdefault("result_out", None)
    kwargs.setdefault("result_fd", None)
    kwargs.setdefault("idempotency_key", None)
    return patch_cmd.patch_command(
        endpoint=endpoint, record_id=record_id, data=data, etag=etag, yes=yes, **kwargs,
    )


class TestValidData:
    def test_inline_json_literal_parsed(self, cli_state, fake_client):
        _run(data='{"a": 1, "b": "two"}')
        body_arg = fake_client.patch.await_args.args[2]
        assert body_arg == {"a": 1, "b": "two"}

    def test_data_from_file(self, cli_state, fake_client, tmp_path: Path):
        f = tmp_path / "payload.json"
        f.write_text('{"loaded": "from-file"}', encoding="utf-8")
        _run(data=f"@{f}")
        body_arg = fake_client.patch.await_args.args[2]
        assert body_arg == {"loaded": "from-file"}


class TestMalformedData:
    def test_malformed_inline_json_raises_bad_parameter(self, cli_state, fake_client):
        with pytest.raises(typer.BadParameter):
            _run(data="{not valid json")
        fake_client.patch.assert_not_called()

    def test_bare_file_path_without_at_prefix_hints_the_fix(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        f = tmp_path / "payload.json"
        f.write_text('{"x": 1}', encoding="utf-8")
        with pytest.raises(typer.BadParameter) as exc_info:
            _run(data=str(f))
        assert "looks like a file path" in str(exc_info.value)
        assert f"-d @{f}" in str(exc_info.value)
        fake_client.patch.assert_not_called()

    def test_missing_at_file_keeps_file_not_found(self, cli_state, fake_client):
        with pytest.raises(typer.BadParameter) as exc_info:
            _run(data="@/no/such/file.json")
        assert "File not found" in str(exc_info.value)
        fake_client.patch.assert_not_called()

    def test_malformed_at_file_names_the_file(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(typer.BadParameter) as exc_info:
            _run(data=f"@{f}")
        assert str(f) in str(exc_info.value)
        fake_client.patch.assert_not_called()


class TestEnvelope:
    def test_envelope_written_on_success(self, cli_state, fake_client, tmp_path: Path):
        out = tmp_path / "env.json"
        _run(result_out=out)
        env = json.loads(out.read_text())
        assert env["status"] == "succeeded"
        assert env["method"] == "PATCH"
        assert env["endpoint"] == "vendors"
