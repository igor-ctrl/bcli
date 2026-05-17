"""Ledger schema v2 — idempotency_key column + migration (AIP §Phase 4d).

Phase 3 shipped schema_version=1. Phase 4d adds:

* New ``step.idempotency_key`` TEXT column (nullable for un-keyed writes).
* SCHEMA_VERSION bumped to ``2``; opening an existing v1 ledger DB runs
  ``ALTER TABLE step ADD COLUMN idempotency_key TEXT`` then stamps the
  version row — preserves existing rows.
* ``Ledger.write_intent`` accepts ``idempotency_key=`` and persists it.
* ``Ledger.find_committed_idempotent_step(key)`` returns the prior
  ``StepRow`` if any step in this run already committed with that key —
  the same-run replay protection.

Cross-run collision detection is *out of scope* for v0.1; documented in
the PR body. Same-run protection covers the agent-retry case where the
runtime re-issues the same step within one batch.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bcli.batch.ledger import SCHEMA_VERSION, Ledger


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    d = tmp_path / "batch"
    d.mkdir()
    return d


# ─── Schema version + column ─────────────────────────────────────────


def test_schema_version_bumped_to_2():
    """The module constant is the public version marker."""
    assert SCHEMA_VERSION == 2


def test_fresh_ledger_has_idempotency_key_column(base_dir):
    ledger = Ledger(run_id="x", base_dir=base_dir)
    ledger.start_run(
        manifest_path="m.yaml", manifest_hash="h",
        profile="p", environment="Sandbox", company="C",
    )
    with sqlite3.connect(ledger.db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(step)")}
    assert "idempotency_key" in cols
    ledger.close()


def test_migration_from_v1_preserves_rows(base_dir, tmp_path: Path):
    """Open a hand-crafted v1 DB; auto-migrate; existing step row survives."""
    db_path = base_dir / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE run (
                run_id TEXT PRIMARY KEY,
                manifest_path TEXT, manifest_hash TEXT,
                profile TEXT, environment TEXT, company TEXT,
                state TEXT,
                started_at TEXT, finished_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE step (
                step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                intent_ts TEXT NOT NULL,
                method TEXT NOT NULL,
                url TEXT NOT NULL,
                body_hash TEXT,
                outcome_ts TEXT,
                status TEXT,
                bc_correlation_id TEXT,
                error_message TEXT,
                rollback_url TEXT
            )
            """
        )
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version VALUES (1)")
        conn.execute(
            "INSERT INTO run (run_id, state, started_at) VALUES ('legacy', 'running', 'T0')"
        )
        conn.execute(
            "INSERT INTO step (run_id, seq, intent_ts, method, url) "
            "VALUES ('legacy', 1, 'T0', 'POST', 'http://x/y')"
        )

    # Opening triggers schema migration via _ensure_schema.
    ledger = Ledger(run_id="legacy", base_dir=base_dir)
    steps = ledger.get_steps("legacy")
    assert len(steps) == 1, "migration must preserve existing step rows"
    assert steps[0]["method"] == "POST"
    # New column is present and defaulted to NULL.
    assert "idempotency_key" in steps[0]
    assert steps[0]["idempotency_key"] is None

    # schema_version row updated to 2.
    with sqlite3.connect(ledger.db_path) as conn:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 2
    ledger.close()


# ─── write_intent persists the key ───────────────────────────────────


def test_write_intent_persists_idempotency_key(base_dir):
    ledger = Ledger(run_id="r1", base_dir=base_dir)
    ledger.start_run(
        manifest_path="m", manifest_hash="h",
        profile="p", environment="Sandbox", company="C",
    )
    step_id = ledger.write_intent(
        seq=1, method="POST", url="http://x/y", body_hash="abc",
        idempotency_key="op-key-123",
    )
    rows = ledger.get_steps("r1")
    assert len(rows) == 1
    assert rows[0]["step_id"] == step_id
    assert rows[0]["idempotency_key"] == "op-key-123"
    ledger.close()


def test_write_intent_without_key_keeps_column_null(base_dir):
    """Backwards-compat: existing callers don't need to pass the key."""
    ledger = Ledger(run_id="r2", base_dir=base_dir)
    ledger.start_run(
        manifest_path="m", manifest_hash="h",
        profile="p", environment="Sandbox", company="C",
    )
    ledger.write_intent(seq=1, method="POST", url="http://x/y", body_hash="abc")
    rows = ledger.get_steps("r2")
    assert rows[0]["idempotency_key"] is None
    ledger.close()


# ─── Same-run replay protection ──────────────────────────────────────


def test_find_committed_idempotent_step_returns_match(base_dir):
    """A prior committed step with the same key is detectable."""
    ledger = Ledger(run_id="r3", base_dir=base_dir)
    ledger.start_run(
        manifest_path="m", manifest_hash="h",
        profile="p", environment="Sandbox", company="C",
    )
    step_id = ledger.write_intent(
        seq=1, method="POST", url="http://x/y", body_hash="abc",
        idempotency_key="dup-key",
    )
    ledger.write_outcome(
        step_id=step_id, status="committed",
        bc_correlation_id="corr-1", error_message=None, rollback_url=None,
    )

    prior = ledger.find_committed_idempotent_step("dup-key")
    assert prior is not None
    assert prior["step_id"] == step_id
    assert prior["status"] == "committed"
    assert prior["bc_correlation_id"] == "corr-1"
    ledger.close()


def test_find_committed_idempotent_step_ignores_uncommitted(base_dir):
    """Intent rows that didn't reach ``committed`` aren't matches.

    An intent row with ``outcome_ts=NULL`` (process died mid-call) is a
    classic SIGKILL case — the operator should be free to retry,
    same-key, and the new attempt should NOT be refused.
    """
    ledger = Ledger(run_id="r4", base_dir=base_dir)
    ledger.start_run(
        manifest_path="m", manifest_hash="h",
        profile="p", environment="Sandbox", company="C",
    )
    ledger.write_intent(
        seq=1, method="POST", url="http://x/y", body_hash="abc",
        idempotency_key="resumable",
    )
    # No write_outcome → outcome_ts is NULL.
    assert ledger.find_committed_idempotent_step("resumable") is None

    # Even an explicit failed outcome is replayable.
    step_id = ledger.write_intent(
        seq=2, method="POST", url="http://x/y2", body_hash="abc",
        idempotency_key="failed-once",
    )
    ledger.write_outcome(
        step_id=step_id, status="failed",
        bc_correlation_id=None, error_message="boom", rollback_url=None,
    )
    assert ledger.find_committed_idempotent_step("failed-once") is None
    ledger.close()


def test_find_committed_idempotent_step_returns_none_for_missing(base_dir):
    ledger = Ledger(run_id="r5", base_dir=base_dir)
    ledger.start_run(
        manifest_path="m", manifest_hash="h",
        profile="p", environment="Sandbox", company="C",
    )
    assert ledger.find_committed_idempotent_step("never-seen") is None
    ledger.close()
