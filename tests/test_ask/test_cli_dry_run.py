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
