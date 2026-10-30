"""``bcli batch run`` writes an intent row before each HTTP call and an
outcome row after, surviving SIGKILL in the gap.

These are integration tests against ``run_batch`` — they patch HOME so the
ledger lands in tmp_path, drive the real client against the ``fake_bc``
transport, and assert on what was sent and on the resulting SQLite file.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.commands.batch_cmd import run_batch


# ─── Fixtures ────────────────────────────────────────────────────────


def _writable_config() -> BCConfig:
    return BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={
            "dev": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                company_id="c-1",
                disable_writes=False,
            ),
        },
    )


@pytest.fixture
def writable_state():
    state._config = _writable_config()
    state._registry = None
    state.profile_name = None
    state.dry_run = False
    state.quiet = True
    yield
    state._config = None
    state._registry = None


@pytest.fixture
def ledger_home(tmp_path, monkeypatch):
    """Force the ledger to live under tmp_path/.config/bcli/batch."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Some platforms also look up Path.home() via pwd; patch directly.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ledger_dir = tmp_path / ".config" / "bcli" / "batch"
    yield ledger_dir


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return f


def _run(f: Path) -> None:
    run_batch(
        file=f, dry_run=False, output=None, format=None,
        set_params=None, params_file=None, yes=True,
    )


def _steps(ledger_home: Path) -> list[dict]:
    db_files = list(ledger_home.glob("*.db"))
    assert len(db_files) == 1
    with sqlite3.connect(db_files[0]) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM step ORDER BY seq")]


# ─── Intent before HTTP ──────────────────────────────────────────────


class TestIntentBeforeHttp:
    def test_intent_written_before_post(
        self, writable_state, ledger_home, fake_bc, tmp_path, monkeypatch,
    ):
        """When the POST reaches the transport, an intent row must
        already exist in the ledger DB. The test enforces ordering by
        reading the DB at the moment of the request.
        """
        send = fake_bc.request

        async def checking_request(method, url, **kwargs):
            # Scan the batch ledger dir for any *.db that contains an
            # intent row for this POST. Done at call time so we catch a
            # write-order regression.
            db_files = list(ledger_home.glob("*.db"))
            assert db_files, "ledger DB should exist before HTTP fires"
            rows: list = []
            for db in db_files:
                with sqlite3.connect(db) as conn:
                    conn.row_factory = sqlite3.Row
                    rows.extend(
                        dict(r) for r in conn.execute(
                            "SELECT * FROM step WHERE method = 'POST'"
                        )
                    )
            assert any(r["intent_ts"] for r in rows), (
                "intent row must be written before HTTP"
            )
            assert all(r["outcome_ts"] is None for r in rows), (
                "outcome must NOT be written before HTTP returns"
            )
            return await send(method, url, **kwargs)

        monkeypatch.setattr(fake_bc, "request", checking_request)

        f = _write_yaml(tmp_path, "p.yaml", """
            steps:
              - name: create
                action: post
                endpoint: items
                data:
                  displayName: "ok"
        """)
        _run(f)

        assert [(s.method, s.json_body) for s in fake_bc.sent] == [
            ("POST", {"displayName": "ok"}),
        ]
        # Post-run: outcome row populated; intent URL is the URL sent.
        rows = _steps(ledger_home)
        assert len(rows) == 1
        assert rows[0]["status"] == "committed"
        assert rows[0]["outcome_ts"] is not None
        assert rows[0]["method"] == "POST"
        assert rows[0]["url"] == fake_bc.last.url
        assert rows[0]["url"].endswith("/items")

    def test_partially_committed_after_simulated_crash(
        self, writable_state, ledger_home, fake_bc, tmp_path,
    ):
        """Step 1 succeeds; step 2 'crashes' (raises mid-flight) — the run
        ledger surfaces ``partially_committed`` on read.
        """
        # Simulate SIGKILL between intent + outcome on step 2 by raising a
        # BaseException that batch_cmd cannot catch with `except Exception:`.
        fake_bc.reply({"id": "rec-1"}, BaseException("simulated SIGKILL"))

        f = _write_yaml(tmp_path, "two.yaml", """
            steps:
              - name: s1
                action: post
                endpoint: items
                data: {x: 1}
              - name: s2
                action: post
                endpoint: items
                data: {x: 2}
        """)
        with pytest.raises(BaseException, match="simulated SIGKILL"):
            _run(f)

        # Read the ledger after the crash.
        from bcli.batch.ledger import Ledger

        rows = Ledger.list_runs(base_dir=ledger_home)
        assert len(rows) == 1
        # Either persisted via SIGKILL-safe handler OR derived on read —
        # the contract is "the operator sees partially_committed".
        assert rows[0]["state"] == "partially_committed"

    def test_normal_completion_marks_completed(
        self, writable_state, ledger_home, fake_bc, tmp_path,
    ):
        f = _write_yaml(tmp_path, "ok.yaml", """
            steps:
              - name: s1
                action: post
                endpoint: items
                data: {x: 1}
        """)
        _run(f)

        from bcli.batch.ledger import Ledger
        rows = Ledger.list_runs(base_dir=ledger_home)
        assert len(rows) == 1
        assert rows[0]["state"] == "completed"


# ─── Rollback URL capture ────────────────────────────────────────────


class TestRollbackUrlCapture:
    def test_post_step_stores_rollback_url(
        self, writable_state, ledger_home, fake_bc, tmp_path,
    ):
        """POST that returns an ``id`` → rollback_url = entity_url(id)."""
        fake_bc.reply({"id": "VND-1"})
        f = _write_yaml(tmp_path, "p.yaml", """
            steps:
              - name: create_vendor
                action: post
                endpoint: vendors
                data: {name: "AAR"}
        """)
        _run(f)

        row = _steps(ledger_home)[0]
        assert row["rollback_url"] == f"{fake_bc.last.url}(VND-1)"


# ─── Batch writes go through the SDK write operation ─────────────────


class TestBatchWriteOperation:
    def test_post_outcome_records_bc_correlation_id(
        self, writable_state, ledger_home, fake_bc, tmp_path,
    ):
        f = _write_yaml(tmp_path, "p.yaml", """
            steps:
              - name: create
                action: post
                endpoint: items
                data: {x: 1}
        """)
        _run(f)

        assert _steps(ledger_home)[0]["bc_correlation_id"] == "corr-test"

    def test_post_step_writes_one_audit_entry(
        self, writable_state, ledger_home, fake_bc, tmp_path,
    ):
        import json

        from bcli.config._model import AuditConfig

        audit_file = tmp_path / "audit.jsonl"
        state._config = state._config.model_copy(
            update={"audit": AuditConfig(enabled=True, path=str(audit_file))},
        )
        f = _write_yaml(tmp_path, "p.yaml", """
            steps:
              - name: create
                action: post
                endpoint: items
                data: {x: 1}
        """)
        _run(f)

        entries = [json.loads(line) for line in audit_file.read_text().splitlines()]
        assert len(entries) == 1
        assert entries[0]["outcome"] == "completed"
        assert entries[0]["method"] == "POST"
        assert entries[0]["resolved_url"] == fake_bc.last.url
        assert entries[0]["correlation_id"] == "corr-test"

    def test_step_route_override_sends_to_custom_api(
        self, writable_state, ledger_home, fake_bc, tmp_path,
    ):
        f = _write_yaml(tmp_path, "p.yaml", """
            steps:
              - name: create
                action: post
                endpoint: widgets
                publisher: acme
                group: ops
                version: v1.0
                data: {x: 1}
        """)
        _run(f)

        assert "/api/acme/ops/v1.0/" in fake_bc.last.url
        assert fake_bc.last.url.endswith("/widgets")
