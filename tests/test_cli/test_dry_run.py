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
def configured_state():
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
        assert "api.businesscentral.dynamics.com" in captured.err

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
        # Stub make_async_client so the client's resolve would lie if
        # called. force_standard=True must bypass it entirely.
        class _LyingClient:
            def resolve(self, *_a, **_kw):
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
        self, configured_state, capsys
    ):
        """If the registry can't resolve the entity (e.g. typo, missing
        custom registry entry), ``render_dry_run`` (still used by
        ``attach upload``) emits — with resolved_url=None — so the user sees
        what they asked for and can correct."""
        state.profile.disable_standard_api = True
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


@pytest.fixture
def real_resolver_state(configured_state):
    """``configured_state`` under the name these tests were written against.

    The suite once stubbed the resolver, which is why an empty ``record_id``
    could render a clean preview: nothing built a real URL. Every test here
    now uses the real resolver.
    """
    yield


class TestDryRunMustNotSucceedWhereTheRealRunFails:
    """A preview that reports success for input the real request refuses is worse
    than no preview: ``--dry-run`` exists so an agent can decide whether to
    proceed, and its documented consumers parse the JSON envelope.

    ``try_resolve_url`` deliberately never raises, so a resolution failure
    records ``resolved_url: null`` and the preview continues. That is right for
    an incidental failure (registry miss, no company id) but wrong for invalid
    *input*, because the real command validates the same value and exits 1. An
    empty ``record_id`` used to render a clean DELETE preview and exit 0 while
    ``bcli delete <entity> ""`` exited 1.
    """

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_empty_record_id_fails_the_preview(self, real_resolver_state, empty):
        state.format = "json"
        with pytest.raises(typer.Exit) as excinfo:
            render_dry_run("DELETE", "items", record_id=empty)
        assert excinfo.value.exit_code == 1

    def test_traversing_record_id_fails_the_preview(self, real_resolver_state):
        state.format = "json"
        with pytest.raises(typer.Exit) as excinfo:
            render_dry_run("DELETE", "items", record_id="1)/../../glEntries('X'")
        assert excinfo.value.exit_code == 1

    def test_the_failure_is_reported_not_traced(self, real_resolver_state, capsys):
        """The dry-run branch sits above each command's own try/except, so a raw
        raise would surface as a traceback rather than the ``Error:`` line the
        real run prints."""
        state.format = "json"
        with pytest.raises(typer.Exit):
            render_dry_run("DELETE", "items", record_id="")
        assert "must not be empty" in capsys.readouterr().err

    def test_none_record_id_still_previews_cleanly(self, real_resolver_state):
        """A collection-targeted write is a real thing; don't break it."""
        state.format = "json"
        with pytest.raises(typer.Exit) as excinfo:
            render_dry_run("POST", "items", body={"x": 1}, record_id=None)
        assert excinfo.value.exit_code == 0

    def test_a_valid_key_still_previews_cleanly(self, real_resolver_state):
        state.format = "json"
        with pytest.raises(typer.Exit) as excinfo:
            render_dry_run("DELETE", "items", record_id="'V00010'")
        assert excinfo.value.exit_code == 0


class TestTryResolveUrlStrictMode:
    def test_strict_re_raises_input_validation(self, real_resolver_state):
        from bcli_cli._url_resolve import try_resolve_url

        with pytest.raises(ValueError, match="must not be empty"):
            try_resolve_url("items", record_id="", strict=True)

    def test_non_strict_still_swallows_input_validation(self, real_resolver_state):
        """The audit path must never break a command that already ran."""
        from bcli_cli._url_resolve import try_resolve_url

        assert try_resolve_url("items", record_id="") is None

    def test_strict_still_swallows_incidental_failures(self, real_resolver_state):
        """A registry miss is not the caller's input being wrong, so even strict
        mode returns None — both the preview and audit paths want that. This
        profile has disable_standard_api unset, so an unknown entity falls
        through to the standard route and resolves; force the registry gate on
        to get a genuine incidental failure."""
        from bcli_cli._url_resolve import try_resolve_url

        state.profile.disable_standard_api = True
        state._registry = None
        assert try_resolve_url("definitelyNotAnEndpoint", record_id="x", strict=True) is None


def _post_dry_run(endpoint: str, **kwargs) -> None:
    from bcli_cli.commands import post_cmd

    state.dry_run = True
    try:
        post_cmd.post_command(
            endpoint=endpoint, data='{"x": 1}', format="json", publisher=None,
            group=None, version=None, yes=True, result_out=None, result_fd=None,
            idempotency_key=None, **kwargs,
        )
    finally:
        state.dry_run = False


class TestWriteCommandDryRun:
    """``post/patch/delete/action --dry-run`` preview the SDK's write plan."""

    def test_post_previews_the_plan_and_sends_nothing(
        self, real_resolver_state, fake_bc, capsys,
    ):
        with pytest.raises(typer.Exit) as excinfo:
            _post_dry_run("items")
        assert excinfo.value.exit_code == 0
        assert fake_bc.sent == []

        payload = _capture_json_payload(capsys)
        assert payload["method"] == "POST"
        assert payload["resolved_url"].endswith("/companies(c-123)/items")
        assert payload["body"] == {"x": 1}
        assert "etag" not in payload
        keys = list(payload)
        assert keys[keys.index("resolved_url") + 1] == "profile"

    def test_post_to_endpoint_blocked_by_disable_standard_api_fails(
        self, real_resolver_state, fake_bc, capsys,
    ):
        """Previously the preview printed ``resolved_url: null`` and exited 0;
        the plan now fails exactly where the real run would."""
        state.profile.disable_standard_api = True
        with pytest.raises(typer.Exit) as excinfo:
            _post_dry_run("definitelyNotAnEndpoint")
        assert excinfo.value.exit_code == 1
        assert fake_bc.sent == []
        assert "Error:" in capsys.readouterr().err
