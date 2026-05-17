"""Unit tests for the SQLite Ledger — schema, write paths, derivations.

These tests target ``bcli.batch.ledger.Ledger`` directly. CLI-level behavior
(``bcli batch run`` writing intent before HTTP, the ``state/list/rollback``
commands) is covered in ``test_batch_cmd_ledger.py`` and
``test_rollback_cmd.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bcli.batch.ledger import Ledger, RunLedgerExistsError


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    """Per-test ledger root."""
    d = tmp_path / "batch"
    d.mkdir()
    return d


# ─── Schema + creation ───────────────────────────────────────────────


class TestLedgerSchema:
    def test_ledger_created_at_run_start(self, base_dir):
        """Opening a Ledger + start_run creates <run-id>.db with `run` + `step` tables."""
        ledger = Ledger(run_id="abc", base_dir=base_dir)
        ledger.start_run(
            manifest_path="/x/y.yaml",
            manifest_hash="deadbeef",
            profile="finance_sandbox",
            environment="Sandbox",
            company="BTUSALLC",
        )

        db_path = base_dir / "abc.db"
        assert db_path.exists()

        with sqlite3.connect(db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert {"run", "step", "schema_version"}.issubset(tables)

    def test_ledger_run_metadata(self, base_dir):
        ledger = Ledger(run_id="run-meta", base_dir=base_dir)
        ledger.start_run(
            manifest_path="/path/to/wf.yaml",
            manifest_hash="cafe",
            profile="prod",
            environment="Production",
            company="BTALI",
        )

        run = ledger.get_run("run-meta")
        assert run["run_id"] == "run-meta"
        assert run["manifest_path"] == "/path/to/wf.yaml"
        assert run["manifest_hash"] == "cafe"
        assert run["profile"] == "prod"
        assert run["environment"] == "Production"
        assert run["company"] == "BTALI"
        assert run["state"] == "running"
        assert run["started_at"]  # ISO timestamp populated

    def test_schema_version_recorded(self, base_dir):
        Ledger(run_id="r", base_dir=base_dir).start_run(
            manifest_path="", manifest_hash="", profile="p", environment="e", company="c",
        )
        with sqlite3.connect(base_dir / "r.db") as conn:
            (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        # AIP §Phase 4d bumped SCHEMA_VERSION 1 → 2 to introduce the
        # ``step.idempotency_key`` column. Migration on existing v1
        # ledgers is exercised in ``test_ledger_idempotency.py``.
        assert version == 2

    def test_state_enum_allowed_values(self, base_dir):
        """Run.state CHECK enum includes every value in the contract."""
        ledger = Ledger(run_id="enum", base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="", profile="p", environment="e", company="c",
        )
        allowed = (
            "planned", "running", "partially_committed", "completed",
            "failed", "cancelled", "rolled_back",
        )
        for state_value in allowed:
            ledger.finish_run("enum", state_value)
            assert ledger.get_run("enum")["state"] == state_value

        # Bad value rejected by the CHECK constraint.
        with pytest.raises(sqlite3.IntegrityError):
            ledger.finish_run("enum", "not_a_real_state")

    def test_rerun_with_same_run_id_errors(self, base_dir):
        """Defensive: start_run twice on the same run_id raises."""
        ledger = Ledger(run_id="dupe", base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="", profile="p", environment="e", company="c",
        )

        ledger2 = Ledger(run_id="dupe", base_dir=base_dir)
        with pytest.raises(RunLedgerExistsError):
            ledger2.start_run(
                manifest_path="", manifest_hash="",
                profile="p", environment="e", company="c",
            )


# ─── Intent + outcome rows ───────────────────────────────────────────


class TestIntentAndOutcome:
    def test_write_intent_returns_step_id(self, base_dir):
        ledger = Ledger(run_id="r", base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="", profile="p", environment="e", company="c",
        )

        sid = ledger.write_intent(
            seq=1, method="POST", url="https://x/api/v2.0/vendors",
            body_hash="bh-1",
        )
        assert isinstance(sid, int)
        assert sid >= 1

        rows = ledger.get_steps("r")
        assert len(rows) == 1
        row = rows[0]
        assert row["seq"] == 1
        assert row["method"] == "POST"
        assert row["url"] == "https://x/api/v2.0/vendors"
        assert row["body_hash"] == "bh-1"
        assert row["intent_ts"]
        # Outcome fields empty until write_outcome is called.
        assert row["outcome_ts"] is None
        assert row["status"] is None

    def test_write_outcome_marks_committed(self, base_dir):
        ledger = Ledger(run_id="r", base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="", profile="p", environment="e", company="c",
        )
        sid = ledger.write_intent(
            seq=1, method="POST", url="https://x/vendors", body_hash="h",
        )
        ledger.write_outcome(
            step_id=sid, status="committed",
            bc_correlation_id="corr-7", error_message=None,
            rollback_url="https://x/vendors(VND-1)",
        )
        rows = ledger.get_steps("r")
        assert rows[0]["status"] == "committed"
        assert rows[0]["bc_correlation_id"] == "corr-7"
        assert rows[0]["rollback_url"] == "https://x/vendors(VND-1)"
        assert rows[0]["error_message"] is None
        assert rows[0]["outcome_ts"]

    def test_write_outcome_failed(self, base_dir):
        ledger = Ledger(run_id="r", base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="", profile="p", environment="e", company="c",
        )
        sid = ledger.write_intent(
            seq=1, method="POST", url="https://x/vendors", body_hash="h",
        )
        ledger.write_outcome(
            step_id=sid, status="failed",
            bc_correlation_id="corr-bad", error_message="500 server error",
            rollback_url=None,
        )
        row = ledger.get_steps("r")[0]
        assert row["status"] == "failed"
        assert row["error_message"] == "500 server error"


# ─── compute_run_state ───────────────────────────────────────────────


class TestComputeRunState:
    def _setup(self, base_dir, run_id="r"):
        ledger = Ledger(run_id=run_id, base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="", profile="p", environment="e", company="c",
        )
        return ledger

    def test_no_steps_yet_is_running(self, base_dir):
        ledger = self._setup(base_dir)
        assert ledger.compute_run_state("r") == "running"

    def test_all_committed_is_completed(self, base_dir):
        ledger = self._setup(base_dir)
        for i in range(1, 4):
            sid = ledger.write_intent(seq=i, method="POST", url=f"u{i}", body_hash="h")
            ledger.write_outcome(
                step_id=sid, status="committed",
                bc_correlation_id=None, error_message=None, rollback_url=None,
            )
        assert ledger.compute_run_state("r") == "completed"

    def test_intent_without_outcome_is_partially_committed(self, base_dir):
        """Step 1 committed, step 2 intent only → simulates SIGKILL between steps."""
        ledger = self._setup(base_dir)
        sid1 = ledger.write_intent(seq=1, method="POST", url="u1", body_hash="h")
        ledger.write_outcome(
            step_id=sid1, status="committed",
            bc_correlation_id=None, error_message=None, rollback_url=None,
        )
        ledger.write_intent(seq=2, method="POST", url="u2", body_hash="h")
        # No outcome for step 2 — process died here.
        assert ledger.compute_run_state("r") == "partially_committed"

    def test_failed_after_committed_is_partially_committed(self, base_dir):
        ledger = self._setup(base_dir)
        sid1 = ledger.write_intent(seq=1, method="POST", url="u1", body_hash="h")
        ledger.write_outcome(
            step_id=sid1, status="committed",
            bc_correlation_id=None, error_message=None, rollback_url=None,
        )
        sid2 = ledger.write_intent(seq=2, method="POST", url="u2", body_hash="h")
        ledger.write_outcome(
            step_id=sid2, status="failed",
            bc_correlation_id=None, error_message="boom", rollback_url=None,
        )
        assert ledger.compute_run_state("r") == "partially_committed"

    def test_only_failures_no_commits_is_failed(self, base_dir):
        ledger = self._setup(base_dir)
        sid = ledger.write_intent(seq=1, method="POST", url="u1", body_hash="h")
        ledger.write_outcome(
            step_id=sid, status="failed",
            bc_correlation_id=None, error_message="boom", rollback_url=None,
        )
        assert ledger.compute_run_state("r") == "failed"


# ─── list_runs ───────────────────────────────────────────────────────


class TestListRuns:
    def test_list_recent_sorted_by_started_at_desc(self, base_dir):
        import time

        for i in range(3):
            ledger = Ledger(run_id=f"run-{i}", base_dir=base_dir)
            ledger.start_run(
                manifest_path=f"/p/{i}", manifest_hash=f"h{i}",
                profile="p", environment="e", company="c",
            )
            ledger.finish_run(f"run-{i}", "completed")
            time.sleep(0.01)  # ensure distinct started_at

        # Scan happens with a class method so we don't keep a stale connection.
        rows = Ledger.list_runs(base_dir=base_dir)
        ids = [r["run_id"] for r in rows]
        # Most recent first
        assert ids == ["run-2", "run-1", "run-0"]
        for r in rows:
            assert r["state"] == "completed"
            assert r["step_count"] == 0

    def test_list_filter_by_state(self, base_dir):
        runs = [
            ("a", "completed"),
            ("b", "failed"),
            ("c", "completed"),
        ]
        for rid, st in runs:
            ledger = Ledger(run_id=rid, base_dir=base_dir)
            ledger.start_run(
                manifest_path="", manifest_hash="",
                profile="p", environment="e", company="c",
            )
            ledger.finish_run(rid, st)

        rows = Ledger.list_runs(base_dir=base_dir, state="failed")
        assert [r["run_id"] for r in rows] == ["b"]

    def test_list_uses_computed_state_for_unfinished_runs(self, base_dir):
        """A SIGKILLed run never had finish_run called. Its DB still says
        'running' — but ``list_runs`` should surface the derived
        ``partially_committed`` so an operator sees reality.
        """
        ledger = Ledger(run_id="ghost", base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="",
            profile="p", environment="e", company="c",
        )
        sid = ledger.write_intent(seq=1, method="POST", url="u1", body_hash="h")
        ledger.write_outcome(
            step_id=sid, status="committed",
            bc_correlation_id=None, error_message=None, rollback_url=None,
        )
        ledger.write_intent(seq=2, method="POST", url="u2", body_hash="h")
        # Process dies — no finish_run, no outcome on step 2.

        rows = Ledger.list_runs(base_dir=base_dir)
        ghost = [r for r in rows if r["run_id"] == "ghost"][0]
        assert ghost["state"] == "partially_committed"

    def test_list_step_count(self, base_dir):
        ledger = Ledger(run_id="r", base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="",
            profile="p", environment="e", company="c",
        )
        for i in range(1, 4):
            ledger.write_intent(seq=i, method="GET", url=f"u{i}", body_hash=None)
        rows = Ledger.list_runs(base_dir=base_dir)
        assert rows[0]["step_count"] == 3


# ─── Durability ──────────────────────────────────────────────────────


class TestDurability:
    def test_wal_mode_enabled(self, base_dir):
        """WAL journal mode + a sync mode that fsyncs commits — required so
        an intent row durably lands before HTTP is dispatched.
        """
        ledger = Ledger(run_id="r", base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="",
            profile="p", environment="e", company="c",
        )
        with sqlite3.connect(base_dir / "r.db") as conn:
            (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
            (synchronous,) = conn.execute("PRAGMA synchronous").fetchone()
        assert mode.lower() == "wal"
        # 1 = NORMAL, 2 = FULL — both flush at commit. 0 (OFF) is unsafe.
        assert synchronous in (1, 2)
