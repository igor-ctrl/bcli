"""Tests for the CLI-side audit wrapper.

The wrapper sits around every write command's actual API call. When the
audit sink is active, it records one entry per call:

* ``completed`` on success
* ``failed`` on exception (captures status_code + correlation_id from BC errors)
* ``dry_run`` for ``--dry-run`` short-circuits

Bodies pass through ``redact`` first so the log never contains plaintext
secrets.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bcli.config._model import AuditConfig, BCConfig, BCDefaults, BCProfile
from bcli.errors import ValidationError
from bcli_cli._audit_wrap import audited_write, emit_dry_run_audit
from bcli_cli._state import state


@pytest.fixture
def audit_state(tmp_path: Path):
    """State with audit enabled, writing JSONL to a tmp_path file."""
    audit_path = tmp_path / "audit-{profile}.jsonl"
    cfg = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={
            "dev": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                company_id="c-123",
            ),
        },
        audit=AuditConfig(enabled=True, path=str(audit_path)),
    )
    state._config = cfg
    state._registry = None
    state.profile_name = None
    state.format = "table"

    yield tmp_path / "audit-dev.jsonl"

    state._config = None
    state._registry = None


@pytest.fixture
def audit_disabled():
    """State with audit explicitly disabled — wrapper must be a no-op."""
    cfg = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={"dev": BCProfile(tenant_id="t1", environment="Sandbox", company_id="c-1")},
        audit=AuditConfig(enabled=False),
    )
    state._config = cfg
    state._registry = None
    state.profile_name = None
    yield
    state._config = None
    state._registry = None


def _read_entries(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


class TestAuditedWrite:
    def test_completed_entry_emitted_on_success(self, audit_state: Path):
        async def _ok():
            return {"id": "abc"}

        result = asyncio.run(
            audited_write(
                _ok(),
                method="POST",
                endpoint="customers",
                body={"displayName": "Test"},
                resolved_url="https://example.test/customers",
            )
        )
        assert result == {"id": "abc"}
        entries = _read_entries(audit_state)
        assert len(entries) == 1
        e = entries[0]
        assert e["method"] == "POST"
        assert e["endpoint"] == "customers"
        assert e["outcome"] == "completed"
        assert e["status"] == 200
        assert e["error"] is None
        assert e["resolved_url"] == "https://example.test/customers"
        assert e["latency_ms"] is not None

    def test_failed_entry_captures_bc_error_metadata(self, audit_state: Path):
        async def _boom():
            raise ValidationError(
                "field 'name' invalid",
                status_code=400,
                correlation_id="xyz-corr-id",
            )

        with pytest.raises(ValidationError):
            asyncio.run(
                audited_write(
                    _boom(),
                    method="POST",
                    endpoint="customers",
                    body={"name": ""},
                )
            )
        entries = _read_entries(audit_state)
        assert len(entries) == 1
        e = entries[0]
        assert e["outcome"] == "failed"
        assert e["status"] == 400
        assert e["correlation_id"] == "xyz-corr-id"
        assert "field 'name' invalid" in e["error"]

    def test_request_body_is_redacted(self, audit_state: Path):
        async def _ok():
            return {"id": "abc"}

        asyncio.run(
            audited_write(
                _ok(),
                method="POST",
                endpoint="users",
                body={"username": "alice", "password": "hunter2", "apiKey": "sk_xxx"},
            )
        )
        entry = _read_entries(audit_state)[0]
        assert entry["request_body"]["username"] == "alice"
        assert entry["request_body"]["password"] != "hunter2"
        assert entry["request_body"]["apiKey"] != "sk_xxx"

    def test_no_emit_when_audit_disabled(self, audit_disabled, tmp_path: Path):
        async def _ok():
            return {}

        asyncio.run(
            audited_write(
                _ok(),
                method="POST",
                endpoint="customers",
                body={"x": 1},
            )
        )
        # Nothing in any audit dir.
        for f in tmp_path.glob("**/*.jsonl"):
            assert False, f"Expected no audit file, found {f}"

    def test_record_id_passed_through_for_patch(self, audit_state: Path):
        async def _ok():
            return {}

        asyncio.run(
            audited_write(
                _ok(),
                method="PATCH",
                endpoint="customers",
                body={"phone": "+1"},
                record_id="abc-123",
            )
        )
        entry = _read_entries(audit_state)[0]
        assert entry["method"] == "PATCH"
        assert entry["record_id"] == "abc-123"

    def test_resolved_url_recorded_when_passed(self, audit_state: Path):
        """The audit-log doc promises ``resolved_url`` is captured for every
        write. Make sure the wrapper stores what callers pass instead of
        silently dropping it to ``null``."""
        async def _ok():
            return {}

        url = "https://api.example.test/v2.0/companies(c-123)/customers"
        asyncio.run(
            audited_write(
                _ok(),
                method="POST",
                endpoint="customers",
                body={"x": 1},
                resolved_url=url,
            )
        )
        entry = _read_entries(audit_state)[0]
        assert entry["resolved_url"] == url

    def test_resolved_url_recorded_on_failure_too(self, audit_state: Path):
        async def _boom():
            raise ValidationError("nope", status_code=400)

        url = "https://api.example.test/v2.0/companies(c-123)/customers"
        with pytest.raises(ValidationError):
            asyncio.run(
                audited_write(
                    _boom(),
                    method="POST",
                    endpoint="customers",
                    body={"x": 1},
                    resolved_url=url,
                )
            )
        entry = _read_entries(audit_state)[0]
        assert entry["outcome"] == "failed"
        assert entry["resolved_url"] == url


_CMD_DEFAULTS = dict(
    format=None, publisher=None, group=None, version=None, yes=True,
    result_out=None, result_fd=None, idempotency_key=None,
)


class TestCommandLevelAuditing:
    """``bcli post/patch/delete`` audit inside the SDK's ``client.write``:
    exactly one entry per write, carrying the URL that was actually sent,
    BC's real status and correlation id, and ``caller="cli"``. A null URL
    there breaks the documented audit contract."""

    def test_post_writes_one_completed_entry(self, audit_state: Path, fake_bc):
        from bcli_cli.commands import post_cmd

        post_cmd.post_command(endpoint="customers", data='{"x": 1}', **_CMD_DEFAULTS)

        entries = _read_entries(audit_state)
        assert len(entries) == 1
        e = entries[0]
        assert e["outcome"] == "completed"
        assert e["status"] == 200
        assert e["correlation_id"] == "corr-test"
        assert e["caller"] == "cli"
        assert e["resolved_url"] == fake_bc.last.url
        assert e["resolved_url"].endswith("/companies(c-123)/customers")

    def test_delete_records_resolved_url_and_record_id(self, audit_state: Path, fake_bc):
        from bcli_cli.commands import delete_cmd

        delete_cmd.delete_command(
            endpoint="items", record_id="rec-1", etag="*", **_CMD_DEFAULTS,
        )

        entry = _read_entries(audit_state)[0]
        assert entry["resolved_url"] == fake_bc.last.url
        assert entry["resolved_url"].endswith("/items(rec-1)")
        assert entry["record_id"] == "rec-1"

    def test_failed_post_records_bc_error_metadata(self, audit_state: Path, fake_bc):
        import typer

        from bcli_cli.commands import post_cmd

        fake_bc.reply(ValidationError("nope", status_code=400, correlation_id="err-corr"))
        with pytest.raises(typer.Exit):
            post_cmd.post_command(endpoint="customers", data='{"x": 1}', **_CMD_DEFAULTS)

        entries = _read_entries(audit_state)
        assert len(entries) == 1
        assert entries[0]["outcome"] == "failed"
        assert entries[0]["status"] == 400
        assert entries[0]["correlation_id"] == "err-corr"
        assert entries[0]["resolved_url"] == fake_bc.last.url


class TestDryRunAudit:
    def test_dry_run_entry_has_outcome_dry_run(self, audit_state: Path):
        emit_dry_run_audit(
            "POST",
            "customers",
            body={"displayName": "T"},
            resolved_url="https://example.test/customers",
        )
        entries = _read_entries(audit_state)
        assert len(entries) == 1
        e = entries[0]
        assert e["outcome"] == "dry_run"
        assert e["status"] is None
        assert e["latency_ms"] is None

    def test_dry_run_no_emit_when_disabled(self, audit_disabled, tmp_path: Path):
        emit_dry_run_audit("POST", "x", body={"a": 1})
        for f in tmp_path.glob("**/*.jsonl"):
            assert False, f"Expected no audit file, found {f}"

    def test_dry_run_redacts_body(self, audit_state: Path):
        emit_dry_run_audit("POST", "users", body={"password": "secret"})
        entry = _read_entries(audit_state)[0]
        assert entry["request_body"]["password"] != "secret"
