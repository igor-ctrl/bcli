"""Policy-violation envelope tests — PR #15 review fix.

A refused write IS an outcome and must produce a ``status="failed"``
envelope. Before the fix, ``confirm_write_or_exit`` ran *before* the
``capture(...)`` block, so a read-only profile + non-interactive + no
``--yes`` exited with code 1 and **no envelope file**, which an agent
runtime can't distinguish from "the command crashed before writing
anything."

The fix moves the gate inside ``capture()``; the salvage path in
``_envelope_wrap.capture`` catches the resulting ``typer.Exit(1)`` and
emits the failed envelope.

Phase 4a renamed the policy-refusal exit from ``1`` to
``EXIT_POLICY`` / ``8`` so an agent can distinguish a deliberate refusal
from a generic crash.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import typer

from bcli_cli._state import state
from bcli_cli.commands import attach_cmd, delete_cmd, patch_cmd, post_cmd


@pytest.fixture(autouse=True)
def force_non_interactive(monkeypatch):
    """``confirm_write_or_exit`` exits 1 only when stdin isn't a TTY +
    no ``--yes`` was passed. Pin both here so every test in this module
    exercises the policy-violation path."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


def _assert_policy_failure_envelope(envelope_path: Path, *, method: str, endpoint: str) -> None:
    """Shared assertion: a refused write left a failed envelope on disk."""
    assert envelope_path.is_file(), (
        f"Expected envelope at {envelope_path} after policy violation; "
        "the command exited without writing one."
    )
    env = json.loads(envelope_path.read_text())
    assert env["status"] == "failed", env
    assert env["exit_code"] == 8, env  # Phase 4a renamed: EXIT_POLICY
    assert env["method"] == method
    assert env["endpoint"] == endpoint
    # Profile context is captured even on a refused write — that's the
    # whole point of the envelope.
    assert env["profile"] == "prod"
    assert env["environment"] == "Production"


class TestPostEnvelopeOnPolicyViolation:
    def test_post_emits_failed_envelope_when_disable_writes_blocks(
        self, readonly_state, fake_client, monkeypatch, tmp_path: Path,
    ):
        monkeypatch.setattr(state, "make_async_client", lambda **_: fake_client)
        out = tmp_path / "post-refused.json"
        with pytest.raises(typer.Exit) as excinfo:
            post_cmd.post_command(
                endpoint="vendors",
                data='{"displayName": "Acme"}',
                format=None,
                publisher=None,
                group=None,
                version=None,
                yes=False,
                result_out=out,
                result_fd=None,
            )
        assert excinfo.value.exit_code == 8
        _assert_policy_failure_envelope(out, method="POST", endpoint="vendors")
        fake_client.post.assert_not_awaited()


class TestPatchEnvelopeOnPolicyViolation:
    def test_patch_emits_failed_envelope_when_disable_writes_blocks(
        self, readonly_state, fake_client, monkeypatch, tmp_path: Path,
    ):
        monkeypatch.setattr(state, "make_async_client", lambda **_: fake_client)
        out = tmp_path / "patch-refused.json"
        with pytest.raises(typer.Exit) as excinfo:
            patch_cmd.patch_command(
                endpoint="vendors",
                record_id="vnd-1",
                data='{"displayName": "Renamed"}',
                etag="*",
                format=None,
                publisher=None,
                group=None,
                version=None,
                yes=False,
                result_out=out,
                result_fd=None,
            )
        assert excinfo.value.exit_code == 8
        _assert_policy_failure_envelope(out, method="PATCH", endpoint="vendors")
        # record_id was captured before the gate refused — confirms the gate
        # is now inside the capture block.
        env = json.loads(out.read_text())
        assert env["record_id"] == "vnd-1"
        fake_client.patch.assert_not_awaited()


class TestDeleteEnvelopeOnPolicyViolation:
    def test_delete_emits_failed_envelope_when_disable_writes_blocks(
        self, readonly_state, fake_client, monkeypatch, tmp_path: Path,
    ):
        monkeypatch.setattr(state, "make_async_client", lambda **_: fake_client)
        out = tmp_path / "delete-refused.json"
        with pytest.raises(typer.Exit) as excinfo:
            delete_cmd.delete_command(
                endpoint="vendors",
                record_id="vnd-1",
                etag="*",
                format=None,
                publisher=None,
                group=None,
                version=None,
                yes=False,
                result_out=out,
                result_fd=None,
            )
        assert excinfo.value.exit_code == 8
        _assert_policy_failure_envelope(out, method="DELETE", endpoint="vendors")
        env = json.loads(out.read_text())
        assert env["record_id"] == "vnd-1"
        fake_client.delete.assert_not_awaited()


class TestAttachUploadEnvelopeOnPolicyViolation:
    def test_attach_upload_emits_failed_envelope_when_disable_writes_blocks(
        self, readonly_state, fake_client, monkeypatch, tmp_path: Path,
    ):
        monkeypatch.setattr(state, "make_async_client", lambda **_: fake_client)
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-fake")
        out = tmp_path / "upload-refused.json"
        with pytest.raises(typer.Exit) as excinfo:
            attach_cmd.upload_command(
                file_path=pdf,
                parent_id="inv-1",
                parent_type="Purchase Invoice",
                file_name=None,
                content_type=None,
                publisher=None,
                group=None,
                version=None,
                standard=False,
                format=None,
                yes=False,
                result_out=out,
                result_fd=None,
            )
        assert excinfo.value.exit_code == 8
        _assert_policy_failure_envelope(
            out, method="UPLOAD", endpoint="documentAttachments",
        )
        fake_client.upload_attachment.assert_not_awaited()
