"""Last-error capture: write/read round-trip, no traceback by default."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from bcli.context import capture_last_error, read_last_error
from bcli.errors import ValidationError


def _make_exc() -> ValidationError:
    exc = ValidationError(
        "bad filter",
        status_code=400,
        bc_message="Field 'junk' is not part of 'vendors'",
        correlation_id="abc-123",
    )
    # Attach attrs that the capture helper looks up via getattr.
    exc.url = "https://example/api?token=secret"  # type: ignore[attr-defined]
    exc.method = "GET"  # type: ignore[attr-defined]
    exc.endpoint = "vendors"  # type: ignore[attr-defined]
    exc.hint = "Run bcli endpoint fields vendors"  # type: ignore[attr-defined]
    return exc


def test_capture_writes_file_without_traceback(tmp_path: Path) -> None:
    exc = _make_exc()
    path = capture_last_error(
        exc=exc,
        command="get vendors",
        profile="production",
        environment="Production",
        company="Contoso",
        debug=False,
        config_dir=tmp_path,
    )
    assert path is not None
    assert path.is_file()
    raw = json.loads(path.read_text())
    assert raw["error_class"] == "ValidationError"
    assert raw["status"] == 400
    assert raw["bc_message"].startswith("Field 'junk'")
    # No traceback in the default file.
    assert raw["traceback_excerpt"] == ""
    # URL query stripped (urlencode may produce %5BREDACTED%5D).
    assert "secret" not in raw["url"]
    assert "REDACTED" in raw["url"]
    # Debug sidecar should NOT exist when debug=False.
    debug_path = tmp_path / "last-error-debug.json"
    assert not debug_path.exists()


def test_capture_writes_debug_sidecar_when_debug_active(tmp_path: Path) -> None:
    try:
        raise _make_exc()
    except ValidationError as e:
        path = capture_last_error(
            exc=e,
            command="get vendors",
            profile="production",
            debug=True,
            config_dir=tmp_path,
        )
    assert path is not None
    debug_path = tmp_path / "last-error-debug.json"
    assert debug_path.is_file()
    raw = json.loads(debug_path.read_text())
    assert raw["traceback_excerpt"]
    assert "Traceback" in raw["traceback_excerpt"]
    # mode 0600 — owner-only.
    mode = stat.S_IMODE(os.stat(debug_path).st_mode)
    assert mode == 0o600, f"expected 0o600 got {oct(mode)}"


def test_read_returns_none_when_no_file(tmp_path: Path) -> None:
    assert read_last_error(config_dir=tmp_path) is None


def test_read_returns_typed_record_round_trip(tmp_path: Path) -> None:
    exc = _make_exc()
    capture_last_error(
        exc=exc,
        command="get vendors",
        profile="production",
        environment="Production",
        company="Contoso",
        debug=False,
        config_dir=tmp_path,
    )
    record = read_last_error(config_dir=tmp_path)
    assert record is not None
    assert record.error_class == "ValidationError"
    assert record.status == 400
    assert record.command == "get vendors"
    assert record.bc_message.startswith("Field 'junk'")
    assert record.traceback_excerpt == ""


def test_capture_is_safe_when_config_dir_unwritable(tmp_path: Path, monkeypatch) -> None:
    # Point at a path under a read-only parent; capture must return
    # None silently, not crash.
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        # Make config_dir a subdir we know mkdir cannot create.
        target = ro / "bcli"
        # On some systems root could still write — protect with a chmod check.
        if os.access(target.parent, os.W_OK):
            pytest.skip("can't simulate read-only parent here")
        path = capture_last_error(
            exc=_make_exc(),
            command="x",
            config_dir=target,
        )
        assert path is None
    finally:
        ro.chmod(0o700)


def test_redacts_bc_message_token_pattern(tmp_path: Path) -> None:
    exc = ValidationError(
        "x",
        bc_message="Inner err: Bearer eyJabc.def.ghi for tenant",
    )
    capture_last_error(exc=exc, command="x", config_dir=tmp_path)
    rec = read_last_error(config_dir=tmp_path)
    assert rec is not None
    assert "Bearer eyJabc.def.ghi" not in rec.bc_message
    assert "[REDACTED]" in rec.bc_message
