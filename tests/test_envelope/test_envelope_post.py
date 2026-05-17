"""Envelope behaviour for ``bcli post``.

The post command is the canonical example. Tests here pin:

* envelope written to ``--result-out PATH`` (success path).
* envelope schema (all spec fields present).
* atomic write contract (uses ``os.replace``).
* stdout output unaffected by the envelope flag.
* envelope written via ``--result-fd N``.
* envelope on HTTP failure (status="failed", correct exit_code).
* envelope on dry_run (no HTTP call, status="succeeded").
* mutual exclusion of ``--result-out`` and ``--result-fd``.
* ``record_id`` extracted from response body.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import typer

from bcli.errors import ServerError, ValidationError
from bcli.exit_codes import EXIT_REMOTE_4XX, EXIT_REMOTE_5XX
from bcli.result_envelope import ENVELOPE_VERSION
from bcli_cli._state import state
from bcli_cli.commands import post_cmd


@pytest.fixture(autouse=True)
def force_non_interactive(monkeypatch):
    """Stdin isn't a TTY in CI — match that in tests too."""
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


def _run_post(endpoint="vendors", data='{"displayName": "Acme"}', **kwargs):
    kwargs.setdefault("format", None)
    kwargs.setdefault("publisher", None)
    kwargs.setdefault("group", None)
    kwargs.setdefault("version", None)
    kwargs.setdefault("yes", False)
    kwargs.setdefault("result_out", None)
    kwargs.setdefault("result_fd", None)
    return post_cmd.post_command(
        endpoint=endpoint,
        data=data,
        **kwargs,
    )


class TestEnvelopeOnSuccess:
    def test_writes_envelope_to_path(self, stub_client, tmp_path: Path):
        out = tmp_path / "out.json"
        _run_post(result_out=out)
        assert out.is_file()
        env = json.loads(out.read_text())
        assert env["version"] == ENVELOPE_VERSION
        assert env["method"] == "POST"
        assert env["endpoint"] == "vendors"
        assert env["status"] == "succeeded"
        assert env["exit_code"] == 0

    def test_envelope_has_all_spec_fields(self, stub_client, tmp_path: Path):
        out = tmp_path / "out.json"
        _run_post(result_out=out)
        env = json.loads(out.read_text())
        for field in (
            "version", "invocation_id", "tool_version", "profile",
            "environment", "company", "method", "endpoint", "resolved_url",
            "record_id", "dry_run", "status", "exit_code", "bc_correlation_id",
            "telemetry_event_id", "audit_log_offset", "started_at", "duration_ms",
        ):
            assert field in env, f"Missing field: {field}"
        # invocation_id should look like a uuid hex
        assert isinstance(env["invocation_id"], str)
        assert len(env["invocation_id"]) >= 16

    def test_envelope_captures_profile_environment_company(
        self, stub_client, tmp_path: Path,
    ):
        out = tmp_path / "out.json"
        _run_post(result_out=out)
        env = json.loads(out.read_text())
        assert env["profile"] == "dev"
        assert env["environment"] == "Sandbox"
        assert env["company"] == "c-123"

    def test_record_id_extracted_from_systemid(
        self, stub_client, tmp_path: Path,
    ):
        stub_client.post.return_value = {"systemId": "vnd-12345", "x": 1}
        out = tmp_path / "out.json"
        _run_post(result_out=out)
        env = json.loads(out.read_text())
        assert env["record_id"] == "vnd-12345"

    def test_record_id_falls_back_to_id_field(
        self, stub_client, tmp_path: Path,
    ):
        stub_client.post.return_value = {"id": "vnd-fallback"}
        out = tmp_path / "out.json"
        _run_post(result_out=out)
        env = json.loads(out.read_text())
        assert env["record_id"] == "vnd-fallback"

    def test_record_id_null_when_response_has_no_id_field(
        self, stub_client, tmp_path: Path,
    ):
        stub_client.post.return_value = {"opaqueResult": True}
        out = tmp_path / "out.json"
        _run_post(result_out=out)
        env = json.loads(out.read_text())
        assert env["record_id"] is None

    def test_resolved_url_populated_on_success(
        self, stub_client, tmp_path: Path,
    ):
        out = tmp_path / "out.json"
        _run_post(result_out=out)
        env = json.loads(out.read_text())
        # try_resolve_url -> AsyncBCClient._resolve_url stub returned this
        assert env["resolved_url"] == "https://example.test/vendors"

    def test_started_at_and_duration_present(
        self, stub_client, tmp_path: Path,
    ):
        out = tmp_path / "out.json"
        _run_post(result_out=out)
        env = json.loads(out.read_text())
        assert env["started_at"] is not None
        assert isinstance(env["duration_ms"], int)
        assert env["duration_ms"] >= 0

    def test_dry_run_field_false_on_real_write(
        self, stub_client, tmp_path: Path,
    ):
        out = tmp_path / "out.json"
        _run_post(result_out=out)
        env = json.loads(out.read_text())
        assert env["dry_run"] is False


class TestAtomicity:
    def test_uses_os_replace(self, stub_client, tmp_path: Path, monkeypatch):
        out = tmp_path / "out.json"
        real_replace = os.replace
        calls: list[tuple[str, str]] = []

        def spy(src, dst):
            calls.append((str(src), str(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr("bcli.result_envelope.os.replace", spy)
        _run_post(result_out=out)
        assert calls, "Expected at least one os.replace call"
        _src, dst = calls[-1]
        assert dst == str(out)


class TestStdoutUnaffected:
    def test_stdout_does_not_contain_envelope(
        self, stub_client, tmp_path: Path, capsys,
    ):
        """The envelope writes to its own channel — stdout still follows
        ``--format`` (here: default table render). Critically, none of the
        envelope JSON keys leak into stdout."""
        out = tmp_path / "out.json"
        _run_post(result_out=out, format="json")
        captured = capsys.readouterr()
        # Envelope-only keys must not appear in stdout
        assert "invocation_id" not in captured.out
        assert "tool_version" not in captured.out
        assert "duration_ms" not in captured.out


class TestResultFdMode:
    def test_writes_envelope_to_fd(self, stub_client, tmp_path: Path):
        r, w = os.pipe()
        try:
            _run_post(result_fd=w)
            buf = b""
            while True:
                chunk = os.read(r, 65536)
                if not chunk:
                    break
                buf += chunk
            env = json.loads(buf)
            assert env["method"] == "POST"
            assert env["status"] == "succeeded"
        finally:
            os.close(r)


class TestMutualExclusion:
    def test_result_out_and_result_fd_mutually_exclusive(
        self, stub_client, tmp_path: Path,
    ):
        with pytest.raises(typer.BadParameter):
            _run_post(result_out=tmp_path / "x.json", result_fd=2)


class TestEnvelopeOnFailure:
    def test_envelope_on_http_4xx(self, stub_client, tmp_path: Path):
        stub_client.post.side_effect = ValidationError(
            "field 'displayName' missing",
            status_code=400,
            correlation_id="corr-400",
        )
        out = tmp_path / "out.json"
        with pytest.raises(typer.Exit):
            _run_post(result_out=out)
        assert out.is_file()
        env = json.loads(out.read_text())
        assert env["status"] == "failed"
        assert env["exit_code"] == EXIT_REMOTE_4XX
        assert env["bc_correlation_id"] == "corr-400"

    def test_envelope_on_http_5xx(self, stub_client, tmp_path: Path):
        stub_client.post.side_effect = ServerError(
            "boom",
            status_code=502,
            correlation_id="corr-5xx",
        )
        out = tmp_path / "out.json"
        with pytest.raises(typer.Exit):
            _run_post(result_out=out)
        env = json.loads(out.read_text())
        assert env["status"] == "failed"
        assert env["exit_code"] == EXIT_REMOTE_5XX
        assert env["bc_correlation_id"] == "corr-5xx"

    def test_envelope_on_generic_exception(self, stub_client, tmp_path: Path):
        stub_client.post.side_effect = RuntimeError("oops")
        out = tmp_path / "out.json"
        with pytest.raises(typer.Exit):
            _run_post(result_out=out)
        env = json.loads(out.read_text())
        assert env["status"] == "failed"
        # Generic errors default to exit_code=1
        assert env["exit_code"] == 1


class TestEnvelopeOnDryRun:
    def test_dry_run_writes_envelope_no_http(
        self, cli_state, stub_resolve_url, tmp_path: Path, monkeypatch,
    ):
        """``--dry-run`` short-circuits HTTP but still drops an envelope so
        an agent driving the CLI can verify the request shape was correct
        without parsing stdout prose."""
        called = {"post": False}

        async def _no_call(*a, **kw):
            called["post"] = True
            return {}

        fake = AsyncMock()
        fake.__aenter__ = AsyncMock(return_value=fake)
        fake.__aexit__ = AsyncMock(return_value=False)
        fake.post = _no_call
        fake._resolve_url = lambda entity, **kw: f"https://example.test/{entity}"
        monkeypatch.setattr(state, "make_async_client", lambda **_: fake)

        state.dry_run = True
        out = tmp_path / "out.json"
        with pytest.raises(typer.Exit) as exc:
            _run_post(result_out=out)
        # render_dry_run uses typer.Exit(); exit_code attr will be 0/None
        assert (exc.value.exit_code or 0) == 0
        assert not called["post"], "Dry-run must NOT issue the HTTP call"
        env = json.loads(out.read_text())
        assert env["dry_run"] is True
        assert env["status"] == "succeeded"
        assert env["exit_code"] == 0


class TestAddedFlagIsOptional:
    def test_envelope_not_written_when_neither_flag_set(
        self, stub_client, tmp_path: Path,
    ):
        """Backward-compat — calling post_command without the envelope flags
        leaves no envelope file anywhere. The flag is strictly opt-in."""
        _run_post()
        # No files should be created in tmp_path
        files = list(tmp_path.glob("**/*"))
        assert files == [], f"Expected no envelope output, found {files}"
