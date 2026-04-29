"""Tests for ``bcli.auth._secure_io`` — private-perms file writer."""

from __future__ import annotations

import os
import sys

import pytest

from bcli.auth._secure_io import (
    _warned_paths,
    warn_if_insecure_perms,
    write_secret_file,
)

posix_only = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX file-mode semantics only checked on POSIX systems",
)


@pytest.fixture(autouse=True)
def _reset_warn_cache():
    """Each test starts with a clean one-shot-warning cache."""
    _warned_paths.clear()
    yield
    _warned_paths.clear()


@posix_only
def test_write_secret_file_sets_0600(tmp_path):
    target = tmp_path / "tokens.json"
    write_secret_file(target, '{"a":1}')
    assert target.read_text() == '{"a":1}'
    mode = target.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


@posix_only
def test_write_secret_file_creates_parent_at_0700(tmp_path):
    target = tmp_path / "nested" / "dir" / "tokens.json"
    write_secret_file(target, '{}')
    assert target.parent.is_dir()
    parent_mode = target.parent.stat().st_mode & 0o777
    assert parent_mode == 0o700, f"expected 0o700, got {oct(parent_mode)}"


@posix_only
def test_write_secret_file_tightens_existing_loose_dir(tmp_path, capsys):
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    target = loose / "tokens.json"
    write_secret_file(target, "x")
    assert (loose.stat().st_mode & 0o777) == 0o700
    err = capsys.readouterr().err
    assert "loose permissions" in err


@posix_only
def test_write_secret_file_atomic_replace_keeps_old_on_error(tmp_path, monkeypatch):
    """If the write blows up, the existing file content survives."""
    target = tmp_path / "tokens.json"
    write_secret_file(target, "original")

    # Force an error after the temp file is opened, before rename.
    real_replace = os.replace

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_secret_file(target, "would-overwrite")

    # Old content still readable; no half-written 0o644 secret on disk.
    monkeypatch.setattr(os, "replace", real_replace)
    assert target.read_text() == "original"


@posix_only
def test_warn_if_insecure_perms_warns_and_tightens(tmp_path, capsys):
    target = tmp_path / "tokens.json"
    target.write_text("x")
    os.chmod(target, 0o644)
    warn_if_insecure_perms(target)
    err = capsys.readouterr().err
    assert "loose permissions" in err
    assert "0o644" in err
    # And it tightens the file in place.
    assert (target.stat().st_mode & 0o777) == 0o600


@posix_only
def test_warn_if_insecure_perms_silent_when_ok(tmp_path, capsys):
    target = tmp_path / "tokens.json"
    target.write_text("x")
    os.chmod(target, 0o600)
    warn_if_insecure_perms(target)
    assert capsys.readouterr().err == ""


@posix_only
def test_warn_only_once_per_path(tmp_path, capsys):
    target = tmp_path / "tokens.json"
    target.write_text("x")
    os.chmod(target, 0o644)
    warn_if_insecure_perms(target)
    # Second call should be silent (already warned).
    os.chmod(target, 0o644)
    warn_if_insecure_perms(target)
    err = capsys.readouterr().err
    # Exactly one warning line for this path.
    assert err.count("loose permissions") == 1


def test_warn_is_noop_when_path_missing(tmp_path):
    """A missing file is not an insecure file — should not error."""
    warn_if_insecure_perms(tmp_path / "no-such-file")
    # No assertion needed; just must not raise.


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_windows_writes_succeed_even_without_chmod_semantics(tmp_path):
    """On Windows, write_secret_file still produces a valid file.

    POSIX permission bits don't fully apply on Windows; the threat model
    there is the user account itself. We just verify no crash.
    """
    target = tmp_path / "tokens.json"
    write_secret_file(target, "x")
    assert target.read_text() == "x"
