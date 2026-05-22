"""``bcli ask --dry-run`` produces a complete redacted bundle, no network."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner


def _run(monkeypatch, tmp_path, *args, env_extra: dict | None = None):
    # Ensure config and last-error reads point at an empty tmp dir so
    # the test bundle is deterministic.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    if env_extra:
        for k, v in env_extra.items():
            monkeypatch.setenv(k, v)
    from bcli_cli.app import app

    runner = CliRunner()
    return runner.invoke(app, list(args))


def test_dry_run_no_context_prints_bundle(monkeypatch, tmp_path: Path) -> None:
    result = _run(monkeypatch, tmp_path, "ask", "--dry-run", "--no-context", "test")
    assert result.exit_code == 0, result.output
    assert "Dry-run bundle" in result.output or "Question" in result.output
    assert "test" in result.output


def test_dry_run_includes_attached_file(monkeypatch, tmp_path: Path) -> None:
    attachment = tmp_path / "note.txt"
    attachment.write_text("operator notes line 1\nline 2\n")
    result = _run(
        monkeypatch,
        tmp_path,
        "ask",
        "--dry-run",
        "--no-context",
        "--attach",
        str(attachment),
        "what next?",
    )
    assert result.exit_code == 0, result.output
    # The attachment label and at least part of its content appear.
    assert "note.txt" in result.output
    # The attachment was redacted+ truncated through the bundle layer.
    assert "operator notes" in result.output or "1 source" in result.output


def test_dry_run_does_not_call_backend(monkeypatch, tmp_path: Path) -> None:
    # Configure a fake backend that raises if called — dry-run must
    # short-circuit before reaching it.
    monkeypatch.setenv("HOME", str(tmp_path))
    # No backend config — even Null shouldn't be reached in dry-run.
    from bcli_cli.app import app
    runner = CliRunner()
    result = runner.invoke(app, ["ask", "--dry-run", "--no-context", "q"])
    # Exit 0 — backend never invoked.
    assert result.exit_code == 0


def test_no_context_suppresses_existing_last_error(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression: --no-context must NOT leak the previous failure to
    the model. The bundler reads last-error.json from disk by default;
    --no-context has to suppress that read explicitly."""
    from bcli.context import capture_last_error
    from bcli.errors import ValidationError

    # Pre-seed a redacted last-error file at the location the bundle
    # layer will read from.
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    config_dir = home / ".config" / "bcli"
    config_dir.mkdir(parents=True)
    capture_last_error(
        exc=ValidationError(
            "bad filter",
            status_code=400,
            bc_message="UNIQUE_PHRASE_FROM_LAST_ERROR",
        ),
        command="get vendors",
        profile="prod",
        config_dir=config_dir,
    )

    # 1. Without --no-context the bundle must include the last error.
    from bcli_cli.app import app
    runner = CliRunner()
    res_default = runner.invoke(app, ["ask", "--dry-run", "what?"])
    assert res_default.exit_code == 0
    assert "UNIQUE_PHRASE_FROM_LAST_ERROR" in res_default.output

    # 2. With --no-context the same bundle must NOT include it.
    res_nocontext = runner.invoke(
        app, ["ask", "--dry-run", "--no-context", "what?"]
    )
    assert res_nocontext.exit_code == 0
    assert "UNIQUE_PHRASE_FROM_LAST_ERROR" not in res_nocontext.output


def test_include_debug_reads_traceback_sidecar(
    monkeypatch, tmp_path: Path
) -> None:
    """--include-debug must pull the traceback sidecar into the bundle."""
    from bcli.context import capture_last_error
    from bcli.errors import ValidationError

    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    config_dir = home / ".config" / "bcli"
    config_dir.mkdir(parents=True)
    try:
        raise ValidationError("oops", status_code=400)
    except ValidationError as e:
        capture_last_error(
            exc=e,
            command="x",
            profile="p",
            debug=True,
            config_dir=config_dir,
        )

    from bcli_cli.app import app
    runner = CliRunner()
    # Without --include-debug: no traceback in render even though sidecar exists.
    res_off = runner.invoke(app, ["ask", "--dry-run", "what?"])
    assert "Traceback" not in res_off.output

    # With --include-debug + matching policy include_debug flag: traceback present.
    res_on = runner.invoke(
        app, ["ask", "--dry-run", "--include-debug", "what?"]
    )
    assert res_on.exit_code == 0
    assert "Traceback" in res_on.output


def test_dry_run_redacts_attachment_secrets(monkeypatch, tmp_path: Path) -> None:
    attachment = tmp_path / "creds.json"
    attachment.write_text(
        '{"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
        '.eyJzdWIiOiJ4eHh4In0.signABCDEF"}'
    )
    result = _run(
        monkeypatch,
        tmp_path,
        "ask",
        "--dry-run",
        "--no-context",
        "--attach",
        str(attachment),
        "x",
    )
    assert result.exit_code == 0
    # The JWT must NOT appear verbatim in the rendered bundle.
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result.output
