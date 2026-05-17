"""Envelope coverage for patch, delete, attach upload, and batch run.

The detailed shape/atomicity contract is pinned in ``test_envelope_post.py``.
Here we only verify the flags exist and the envelope reflects the right
method/endpoint/status for the other verbs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from bcli.errors import ValidationError
from bcli.exit_codes import EXIT_REMOTE_4XX
from bcli_cli.commands import attach_cmd, batch_cmd, delete_cmd, patch_cmd


@pytest.fixture(autouse=True)
def force_non_interactive(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


# ── PATCH ──────────────────────────────────────────────────────────────


def _run_patch(**kwargs):
    defaults = dict(
        endpoint="vendors",
        record_id="vnd-1",
        data='{"displayName": "Renamed"}',
        etag="*",
        format=None,
        publisher=None,
        group=None,
        version=None,
        yes=False,
        result_out=None,
        result_fd=None,
    )
    defaults.update(kwargs)
    return patch_cmd.patch_command(**defaults)


class TestPatchEnvelope:
    def test_patch_writes_envelope(self, stub_client, tmp_path: Path):
        out = tmp_path / "out.json"
        _run_patch(result_out=out)
        env = json.loads(out.read_text())
        assert env["method"] == "PATCH"
        assert env["endpoint"] == "vendors"
        assert env["record_id"] == "vnd-1"
        assert env["status"] == "succeeded"
        assert env["exit_code"] == 0

    def test_patch_envelope_on_failure(self, stub_client, tmp_path: Path):
        stub_client.patch.side_effect = ValidationError(
            "etag mismatch", status_code=412, correlation_id="corr-patch",
        )
        out = tmp_path / "out.json"
        with pytest.raises(typer.Exit):
            _run_patch(result_out=out)
        env = json.loads(out.read_text())
        assert env["status"] == "failed"
        assert env["exit_code"] == EXIT_REMOTE_4XX
        assert env["bc_correlation_id"] == "corr-patch"


# ── DELETE ─────────────────────────────────────────────────────────────


def _run_delete(**kwargs):
    defaults = dict(
        endpoint="vendors",
        record_id="vnd-1",
        etag="*",
        format=None,
        publisher=None,
        group=None,
        version=None,
        yes=False,
        result_out=None,
        result_fd=None,
    )
    defaults.update(kwargs)
    return delete_cmd.delete_command(**defaults)


class TestDeleteEnvelope:
    def test_delete_writes_envelope(self, stub_client, tmp_path: Path):
        out = tmp_path / "out.json"
        _run_delete(result_out=out)
        env = json.loads(out.read_text())
        assert env["method"] == "DELETE"
        assert env["endpoint"] == "vendors"
        assert env["record_id"] == "vnd-1"
        assert env["status"] == "succeeded"

    def test_delete_envelope_on_failure(self, stub_client, tmp_path: Path):
        stub_client.delete.side_effect = ValidationError(
            "not found", status_code=404, correlation_id="corr-del",
        )
        out = tmp_path / "out.json"
        with pytest.raises(typer.Exit):
            _run_delete(result_out=out)
        env = json.loads(out.read_text())
        assert env["status"] == "failed"
        assert env["exit_code"] == EXIT_REMOTE_4XX
        assert env["bc_correlation_id"] == "corr-del"


# ── ATTACH UPLOAD ──────────────────────────────────────────────────────


def _run_upload(file_path: Path, **kwargs):
    defaults = dict(
        file_path=file_path,
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
        result_out=None,
        result_fd=None,
    )
    defaults.update(kwargs)
    return attach_cmd.upload_command(**defaults)


class TestAttachUploadEnvelope:
    def test_attach_upload_writes_envelope(self, stub_client, tmp_path: Path):
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-fake")
        out = tmp_path / "out.json"
        _run_upload(file_path=pdf, result_out=out)
        env = json.loads(out.read_text())
        assert env["method"] == "UPLOAD"
        assert env["endpoint"] == "documentAttachments"
        assert env["status"] == "succeeded"
        # record_id should reflect the attachment id ('id' from the response)
        assert env["record_id"] == "att-9"

    def test_attach_upload_envelope_on_failure(
        self, stub_client, tmp_path: Path,
    ):
        stub_client.upload_attachment.side_effect = ValidationError(
            "parent not found",
            status_code=404,
            correlation_id="corr-att",
        )
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-fake")
        out = tmp_path / "out.json"
        with pytest.raises(typer.Exit):
            _run_upload(file_path=pdf, result_out=out)
        env = json.loads(out.read_text())
        assert env["status"] == "failed"
        assert env["exit_code"] == EXIT_REMOTE_4XX
        assert env["bc_correlation_id"] == "corr-att"


# ── BATCH RUN ──────────────────────────────────────────────────────────


def _write_batch_yaml(tmp_path: Path, body: str) -> Path:
    import textwrap
    f = tmp_path / "batch.yaml"
    f.write_text(textwrap.dedent(body).strip(), encoding="utf-8")
    return f


def _run_batch(file: Path, **kwargs):
    defaults = dict(
        file=file,
        dry_run=False,
        output=None,
        format=None,
        set_params=None,
        params_file=None,
        yes=True,  # bypass the confirmation prompt in tests
        result_out=None,
        result_fd=None,
    )
    defaults.update(kwargs)
    return batch_cmd.run_batch(**defaults)


class TestBatchRunEnvelope:
    def test_batch_run_writes_envelope_all_ok(
        self, stub_client, tmp_path: Path,
    ):
        yaml_file = _write_batch_yaml(tmp_path, """
            name: ok
            steps:
              - name: create_vendor
                action: post
                endpoint: vendors
                data: {displayName: Acme}
        """)
        out = tmp_path / "out.json"
        _run_batch(yaml_file, result_out=out)
        env = json.loads(out.read_text())
        assert env["method"] == "BATCH_RUN"
        # Batch envelope identifies the manifest stem, not a single endpoint
        assert env["endpoint"] == "batch"
        assert env["status"] == "succeeded"
        assert env["exit_code"] == 0
        # `record_id` carries the ledger run id for BATCH_RUN — that's the
        # cross-reference an agent uses to fetch the per-step ledger
        # detail with `bcli batch state <run-id>`. (#15 + #16 integration.)
        assert env["record_id"] is not None
        assert len(env["record_id"]) == 32  # uuid4 hex
        # resolved_url is still not meaningful for a batch (multiple URLs).
        assert env["resolved_url"] is None

    def test_batch_run_envelope_failed_when_any_step_fails(
        self, stub_client, tmp_path: Path,
    ):
        stub_client.post.side_effect = ValidationError(
            "boom", status_code=400, correlation_id="corr-batch",
        )
        yaml_file = _write_batch_yaml(tmp_path, """
            name: oops
            steps:
              - name: create_vendor
                action: post
                endpoint: vendors
                data: {displayName: Acme}
        """)
        out = tmp_path / "out.json"
        with pytest.raises(typer.Exit):
            _run_batch(yaml_file, result_out=out)
        env = json.loads(out.read_text())
        assert env["status"] == "failed"
        # batch failure is a generic exit 1 (Phase 3 will surface step-level
        # info via the ledger; we don't reach for 6/7 at batch level here)
        assert env["exit_code"] == 1
