"""Tests for the shared --out destination helper.

``atomic_write_bytes`` publishes the decoded payload for ``bcli action --out``.
Its ``overwrite`` policy mirrors ``BCTransport.download``: a no-replace commit
so a destination that appears after the CLI's pre-flight check can't be
clobbered (#21 review, VULN-0002).
"""

from __future__ import annotations

import pytest

from bcli_cli._out_path import atomic_write_bytes


def test_overwrite_false_refuses_an_existing_destination(tmp_path):
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"original")

    with pytest.raises(FileExistsError, match="pre-flight"):
        atomic_write_bytes(dest, b"new payload", overwrite=False)

    assert dest.read_bytes() == b"original"          # untouched
    assert list(tmp_path.glob("out.bin.*")) == []     # no temp litter


def test_overwrite_false_writes_a_new_destination(tmp_path):
    dest = tmp_path / "fresh.bin"

    atomic_write_bytes(dest, b"payload", overwrite=False)

    assert dest.read_bytes() == b"payload"
    assert list(tmp_path.glob("fresh.bin.*")) == []


def test_overwrite_true_replaces(tmp_path):
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"original")

    atomic_write_bytes(dest, b"replacement", overwrite=True)

    assert dest.read_bytes() == b"replacement"
