"""Tests for the structured dry-run renderer.

The renderer is the single source of truth for what ``--dry-run`` emits
across every write command (post / patch / delete / attach / batch). When
the active output format is machine-readable (json / ndjson / raw) the
renderer prints a JSON object on stdout that agents can parse before
deciding whether to proceed; when the format is human-facing it prints a
rich panel on stderr instead. Either way it ``typer.Exit()``s with code 0
— dry-run is an explicit user request, not an error.
"""

from __future__ import annotations

import json
from contextlib import suppress

import pytest
import typer

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._dry_run import render_dry_run
from bcli_cli._state import state


@pytest.fixture
def configured_state(monkeypatch):
    """Set up a minimal CLIState with one writable profile.

    Cleans up afterwards so tests don't bleed into each other.
    """
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
    state.format = "table"

    # Default: URL resolution returns a deterministic stub. Individual
    # tests can override by re-monkeypatching make_async_client.
    class _StubClient:
        def _resolve_url(self, entity, **_):
            return f"https://api.example.test/v2.0/companies(c-123)/{entity}"

    monkeypatch.setattr(state, "make_async_client", lambda **_: _StubClient())

    yield
    state._config = None
    state._registry = None
    state.format = "table"


def _capture_json_payload(capsys) -> dict:
    """Run helper and parse the JSON it printed to stdout."""
    out = capsys.readouterr().out
    return json.loads(out)


class TestStructuredOutput:
    def test_json_format_emits_dry_run_envelope(self, configured_state, capsys):
        state.format = "json"
        with pytest.raises(typer.Exit) as excinfo:
            render_dry_run("POST", "customers", body={"displayName": "Test"})
        assert excinfo.value.exit_code == 0

        payload = _capture_json_payload(capsys)
        assert payload["dry_run"] is True
        assert payload["method"] == "POST"
        assert payload["endpoint"] == "customers"
        assert payload["body"] == {"displayName": "Test"}
        assert payload["profile"] == "dev"
        assert payload["environment"] == "Sandbox"
        assert payload["company_id"] == "c-123"
        assert payload["resolved_url"].endswith("/customers")

    def test_patch_includes_record_id(self, configured_state, capsys):
        state.format = "json"
        with suppress(typer.Exit):
            render_dry_run(
                "PATCH",
                "customers",
                body={"phoneNumber": "+1"},
                record_id="abc-123",
            )
        payload = _capture_json_payload(capsys)
        assert payload["method"] == "PATCH"
        assert payload["record_id"] == "abc-123"
        assert payload["body"] == {"phoneNumber": "+1"}

    def test_delete_omits_body(self, configured_state, capsys):
        state.format = "json"
        with suppress(typer.Exit):
            render_dry_run("DELETE", "customers", record_id="abc-123")
        payload = _capture_json_payload(capsys)
        assert payload["method"] == "DELETE"
        assert payload["record_id"] == "abc-123"
        assert "body" not in payload

    def test_extra_fields_merge_into_payload(self, configured_state, capsys):
        """Helper accepts arbitrary extra fields — used by attach/batch."""
        state.format = "json"
        with suppress(typer.Exit):
            render_dry_run(
                "UPLOAD",
                "documentAttachments",
                extra={"file_path": "/tmp/foo.pdf", "byte_size": 1234},
            )
        payload = _capture_json_payload(capsys)
        assert payload["file_path"] == "/tmp/foo.pdf"
        assert payload["byte_size"] == 1234

    def test_method_normalised_to_upper_case(self, configured_state, capsys):
        state.format = "json"
        with suppress(typer.Exit):
            render_dry_run("post", "items")
        payload = _capture_json_payload(capsys)
        assert payload["method"] == "POST"

    def test_ndjson_format_emits_single_line(self, configured_state, capsys):
        state.format = "ndjson"
        with suppress(typer.Exit):
            render_dry_run("POST", "items", body={"x": 1})
        out = capsys.readouterr().out.strip()
        assert "\n" not in out
        assert json.loads(out)["dry_run"] is True


class TestHumanOutput:
    def test_table_format_writes_yellow_warning_to_stderr(
        self, configured_state, capsys
    ):
        state.format = "table"
        with suppress(typer.Exit):
            render_dry_run("POST", "items", body={"x": 1})
        captured = capsys.readouterr()
        assert "POST items" in captured.err
        assert "dry-run" in captured.err.lower()

    def test_human_format_includes_resolved_url(self, configured_state, capsys):
        state.format = "table"
        with suppress(typer.Exit):
            render_dry_run("POST", "items", body={"x": 1})
        captured = capsys.readouterr()
        # URL should appear somewhere in the human output
        assert "api.example.test" in captured.err

    def test_human_format_renders_body_on_stdout(self, configured_state, capsys):
        """The body itself is JSON to stdout so users can pipe it; the
        framing chrome stays on stderr."""
        state.format = "table"
        with suppress(typer.Exit):
            render_dry_run("POST", "items", body={"alpha": "beta"})
        captured = capsys.readouterr()
        assert "alpha" in captured.err or "alpha" in captured.out


class TestForceStandardResolution:
    """When ``force_standard=True`` (e.g. ``bcli attach upload --standard``),
    the dry-run preview URL must reflect the standard /api/v2.0/ route, not
    whatever the registry might say. Otherwise the preview misleads users
    about exactly the escape-hatch case the flag exists for."""

    def test_force_standard_uses_v2_route(self, configured_state, capsys, monkeypatch):
        # Stub make_async_client so the client's _resolve_url would lie if
        # called. force_standard=True must bypass it entirely.
        class _LyingClient:
            def _resolve_url(self, *_a, **_kw):
                return "https://example.test/CUSTOM/PATH/should-not-be-used"

        monkeypatch.setattr(state, "make_async_client", lambda **_: _LyingClient())
        state.format = "json"
        with pytest.raises(typer.Exit):
            render_dry_run(
                "UPLOAD", "documentAttachments",
                force_standard=True,
                extra={"file_path": "/tmp/x"},
            )
        payload = _capture_json_payload(capsys)
        assert payload["resolved_url"] is not None
        assert "/api/v2.0/" in payload["resolved_url"]
        assert "CUSTOM/PATH" not in payload["resolved_url"]


class TestResolutionFailure:
    def test_failed_url_resolution_does_not_break_dry_run(
        self, configured_state, capsys, monkeypatch
    ):
        """If the registry can't resolve the entity (e.g. typo, missing
        custom registry entry), dry-run still emits — with resolved_url=None
        — so the user sees what they asked for and can correct."""

        def _failing_client(**_):
            class _C:
                def _resolve_url(self, *_a, **_kw):
                    raise RuntimeError("registry boom")

            return _C()

        monkeypatch.setattr(state, "make_async_client", _failing_client)
        state.format = "json"
        with suppress(typer.Exit):
            render_dry_run("POST", "totallyMadeUpEntity", body={"x": 1})
        payload = _capture_json_payload(capsys)
        assert payload["resolved_url"] is None
        assert payload["endpoint"] == "totallyMadeUpEntity"


class TestExitBehaviour:
    def test_always_exits_clean(self, configured_state):
        state.format = "json"
        with pytest.raises(typer.Exit) as excinfo:
            render_dry_run("POST", "items", body={"x": 1})
        assert excinfo.value.exit_code == 0

    def test_exits_clean_for_human_format_too(self, configured_state):
        state.format = "table"
        with pytest.raises(typer.Exit) as excinfo:
            render_dry_run("DELETE", "items", record_id="x")
        assert excinfo.value.exit_code == 0
