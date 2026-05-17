"""SQLite-backed operation ledger for ``bcli batch run``.

Why this exists
---------------
Stdout-only observation cannot distinguish "POST committed, stdout died"
from "nothing started."  We need durable, per-step state the runtime can
query after the process dies — and a coherent answer to "what's the
overall state of this run?" derived from the steps.

Durability contract
-------------------
- One SQLite file per run:  ``<base_dir>/<run-id>.db``.
- ``PRAGMA journal_mode=WAL`` + ``synchronous=NORMAL`` so an intent row
  hits disk before the HTTP call is dispatched.
- Autocommit mode (``isolation_level=None``).  Each ``INSERT`` /
  ``UPDATE`` is its own transaction, so a SIGKILL between two
  statements never leaves a half-written row.
- The connection is kept open for the life of the ``Ledger`` instance to
  avoid pathological lock churn on Windows-style filesystems.

State derivation
----------------
``run.state`` is *stamped* on lifecycle calls (``start_run`` →
``"running"``;  ``finish_run`` → whatever the caller declared).  But if
the process is SIGKILLed before ``finish_run`` runs, the stamp lies.
``compute_run_state(run_id)`` is the authoritative read-side derivation
from the step table:

    all committed                       → "completed"
    any committed + any without outcome → "partially_committed"
    any committed + any failed          → "partially_committed"
    only failed                          → "failed"
    only intent (no outcomes)            → "running"  (process still going,
                                                       or died before any
                                                       outcome — caller
                                                       must reconcile with
                                                       finished_at)

``list_runs`` and ``batch state`` both call this on read so the operator
always sees the truthful state, not a stale stamp.

Schema (version 2)
------------------
::

    CREATE TABLE run (
      run_id TEXT PRIMARY KEY,
      manifest_path TEXT,
      manifest_hash TEXT,
      profile TEXT, environment TEXT, company TEXT,
      state TEXT CHECK (state IN (
        'planned','running','partially_committed','completed',
        'failed','cancelled','rolled_back'
      )),
      started_at TEXT, finished_at TEXT
    );
    CREATE TABLE step (
      step_id INTEGER PRIMARY KEY,
      run_id TEXT REFERENCES run(run_id),
      seq INTEGER,
      intent_ts TEXT, method TEXT, url TEXT, body_hash TEXT,
      outcome_ts TEXT,
      status TEXT,             -- "committed" | "failed" | "rollback_skipped" | "rolled_back" | "unknown"
      bc_correlation_id TEXT,
      error_message TEXT,
      rollback_url TEXT,
      idempotency_key TEXT     -- v2 (AIP §Phase 4d)
    );
    CREATE TABLE schema_version (version INTEGER);

The step ``status`` column is intentionally not constrained — rollback
introduces transient states (e.g. ``rollback_skipped``) we don't want to
keep adding to a CHECK enum.

Migrations
----------
``_ensure_schema`` inspects ``schema_version`` on every connect. v1
ledgers get an ``ALTER TABLE step ADD COLUMN idempotency_key TEXT`` and
a version bump — non-destructive, preserves all existing rows. New
ledgers get the column inline.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

# Allowed values for ``run.state``.  Mirrors the contract doc §Phase 3
# enum.  Step-level statuses are deliberately *not* enforced — they
# evolve faster than the run-level enum and have a wider vocabulary
# (``committed``, ``failed``, ``unknown``, ``rollback_skipped``,
# ``rolled_back`` …).
_RUN_STATE_ENUM = (
    "planned",
    "running",
    "partially_committed",
    "completed",
    "failed",
    "cancelled",
    "rolled_back",
)


class RunLedgerExistsError(RuntimeError):
    """Raised when ``start_run`` is called on a run-id that already has a row.

    Defensive: the CLI generates run ids via ``uuid4()`` so natural
    collisions are impossible.  This guards against a programmer mistake
    (re-running ``start_run`` on the same Ledger) corrupting the
    sequence of timestamps.
    """


# ─── Public dataclasses ──────────────────────────────────────────────


@dataclass(frozen=True)
class StepRow:
    """A row from the ``step`` table — exposed for ``rollback`` consumers."""

    step_id: int
    run_id: str
    seq: int
    intent_ts: str
    method: str
    url: str
    body_hash: str | None
    outcome_ts: str | None
    status: str | None
    bc_correlation_id: str | None
    error_message: str | None
    rollback_url: str | None


# ─── Path helpers ────────────────────────────────────────────────────


def _default_base_dir() -> Path:
    """Where ledger files live by default.

    Resolved lazily (via a function call) rather than captured at import
    time so tests can monkeypatch ``Path.home`` without surgery.
    """
    return Path.home() / ".config" / "bcli" / "batch"


# ─── The Ledger ──────────────────────────────────────────────────────


class Ledger:
    """A single-run SQLite ledger.

    Construction does *not* create the DB — that happens on
    ``start_run``.  The split lets callers pass a Ledger handle into
    code paths that read existing runs without inadvertently creating
    files.
    """

    def __init__(self, run_id: str, *, base_dir: Path | None = None) -> None:
        self.run_id = run_id
        self.base_dir = Path(base_dir) if base_dir is not None else _default_base_dir()
        self._conn: sqlite3.Connection | None = None

    # ── Connection lifecycle ────────────────────────────────────────

    @property
    def db_path(self) -> Path:
        return self.base_dir / f"{self.run_id}.db"

    def _connect(self) -> sqlite3.Connection:
        """Open (or reuse) the SQLite connection with durability pragmas."""
        if self._conn is not None:
            return self._conn

        self.base_dir.mkdir(parents=True, exist_ok=True)
        # isolation_level=None ⇒ autocommit.  Each INSERT/UPDATE is its
        # own transaction; combined with WAL + synchronous=NORMAL this
        # gives us "the intent row landed on disk before HTTP fires."
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema(conn)
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Ledger":
        self._connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ── Schema setup ────────────────────────────────────────────────

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        # IMPORTANT: the CHECK enum below is the source of truth for
        # ``run.state`` values.  Adding a new value here is a schema
        # change; bump ``SCHEMA_VERSION`` and add a migration.
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS run (
                run_id        TEXT PRIMARY KEY,
                manifest_path TEXT,
                manifest_hash TEXT,
                profile       TEXT,
                environment   TEXT,
                company       TEXT,
                state         TEXT CHECK (state IN (
                    {",".join(f"'{s}'" for s in _RUN_STATE_ENUM)}
                )),
                started_at    TEXT,
                finished_at   TEXT
            )
            """,
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS step (
                step_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            TEXT NOT NULL REFERENCES run(run_id),
                seq               INTEGER NOT NULL,
                intent_ts         TEXT NOT NULL,
                method            TEXT NOT NULL,
                url               TEXT NOT NULL,
                body_hash         TEXT,
                outcome_ts        TEXT,
                status            TEXT,
                bc_correlation_id TEXT,
                error_message     TEXT,
                rollback_url      TEXT,
                idempotency_key   TEXT
            )
            """,
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
        )
        # Idempotent: only insert if empty.
        existing = conn.execute("SELECT version FROM schema_version").fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        else:
            # Migration: v1 → v2 adds the idempotency_key column. Inspect
            # the live table (rather than just the version row) because a
            # rolled-out v2 client may have created the table inline
            # already — additive-only ALTER is the safe path either way.
            existing_version = int(existing[0])
            if existing_version < 2:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(step)")}
                if "idempotency_key" not in cols:
                    conn.execute(
                        "ALTER TABLE step ADD COLUMN idempotency_key TEXT"
                    )
                conn.execute(
                    "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,)
                )

    # ── Lifecycle ───────────────────────────────────────────────────

    def start_run(
        self,
        *,
        manifest_path: str,
        manifest_hash: str,
        profile: str,
        environment: str,
        company: str,
    ) -> None:
        """Insert the ``run`` row in state ``"running"``.

        Raises ``RunLedgerExistsError`` if a row already exists for this
        ``run_id`` — see class docstring.
        """
        conn = self._connect()
        existing = conn.execute(
            "SELECT 1 FROM run WHERE run_id = ?", (self.run_id,)
        ).fetchone()
        if existing is not None:
            raise RunLedgerExistsError(
                f"Ledger row for run_id={self.run_id!r} already exists. "
                "Use a fresh run-id."
            )
        conn.execute(
            """
            INSERT INTO run (
                run_id, manifest_path, manifest_hash,
                profile, environment, company,
                state, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                self.run_id, manifest_path, manifest_hash,
                profile, environment, company, _utc_now_iso(),
            ),
        )

    def finish_run(self, run_id: str, final_state: str) -> None:
        """Stamp the final state + ``finished_at`` for this run.

        ``final_state`` must be a member of the CHECK enum; SQLite will
        reject anything else with an ``IntegrityError`` which we let
        propagate (callers are the runtime, not end users).
        """
        conn = self._connect()
        conn.execute(
            "UPDATE run SET state = ?, finished_at = ? WHERE run_id = ?",
            (final_state, _utc_now_iso(), run_id),
        )

    # ── Step writes ─────────────────────────────────────────────────

    def write_intent(
        self,
        *,
        seq: int,
        method: str,
        url: str,
        body_hash: str | None,
        idempotency_key: str | None = None,
    ) -> int:
        """Insert the *intent* row for a step and return its ``step_id``.

        This is the row that survives a SIGKILL — we know we *tried* the
        HTTP call.  The matching ``write_outcome`` flips the state once
        the response (or an exception) comes back.

        ``idempotency_key`` (AIP §Phase 4d) is the optional opaque token
        the caller wants to associate with this step. Stored verbatim so
        a same-run replay can be detected via
        :meth:`find_committed_idempotent_step`.
        """
        conn = self._connect()
        cur = conn.execute(
            """
            INSERT INTO step (
                run_id, seq, intent_ts, method, url, body_hash, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (self.run_id, seq, _utc_now_iso(), method, url, body_hash,
             idempotency_key),
        )
        return int(cur.lastrowid)

    def find_committed_idempotent_step(
        self, idempotency_key: str
    ) -> dict[str, Any] | None:
        """Return the prior committed step (if any) with this key in this run.

        AIP §Phase 4d — same-run replay protection. A new write that
        carries an idempotency_key already in the ``committed`` state
        should be refused (the agent retried; the prior call landed).

        Cross-run collision detection is deliberately out of scope for
        v0.1: implementing it requires scanning every ``*.db`` under
        ``batch/`` on each call. Document deferral in the PR.
        """
        if not idempotency_key:
            return None
        conn = self._connect()
        row = conn.execute(
            """
            SELECT * FROM step
             WHERE run_id = ?
               AND idempotency_key = ?
               AND status = 'committed'
             ORDER BY seq ASC
             LIMIT 1
            """,
            (self.run_id, idempotency_key),
        ).fetchone()
        return dict(row) if row is not None else None

    def write_outcome(
        self,
        *,
        step_id: int,
        status: str,
        bc_correlation_id: str | None,
        error_message: str | None,
        rollback_url: str | None,
    ) -> None:
        """Stamp the outcome row for a step.

        ``status`` is the step-level status (free text — see module
        docstring for the vocabulary).  ``rollback_url`` is the
        precomposed inverse-op target for the rollback command;
        callers responsible for building it on POST success.
        """
        conn = self._connect()
        conn.execute(
            """
            UPDATE step
               SET outcome_ts = ?,
                   status = ?,
                   bc_correlation_id = ?,
                   error_message = ?,
                   rollback_url = ?
             WHERE step_id = ?
            """,
            (
                _utc_now_iso(), status, bc_correlation_id,
                error_message, rollback_url, step_id,
            ),
        )

    def update_step_status(self, step_id: int, status: str) -> None:
        """Used by rollback to flip a step to ``rolled_back`` /
        ``rollback_skipped`` after the inverse op fires."""
        conn = self._connect()
        conn.execute(
            "UPDATE step SET status = ? WHERE step_id = ?",
            (status, step_id),
        )

    # ── Reads ───────────────────────────────────────────────────────

    def get_run(self, run_id: str) -> dict[str, Any]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"No run row for run_id={run_id!r}")
        return dict(row)

    def get_steps(self, run_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM step WHERE run_id = ? ORDER BY seq", (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Derived state ───────────────────────────────────────────────

    def compute_run_state(self, run_id: str) -> str:
        """Derive the *real* state of a run from its step rows.

        Stamped ``run.state`` is just a hint — it goes stale the moment
        the process dies between ``write_outcome`` and ``finish_run``.
        This method is the source of truth for the read side.
        """
        conn = self._connect()
        rows = conn.execute(
            "SELECT status, outcome_ts FROM step WHERE run_id = ?", (run_id,),
        ).fetchall()
        run_row = conn.execute(
            "SELECT state, finished_at FROM run WHERE run_id = ?", (run_id,),
        ).fetchone()
        stamped = run_row["state"] if run_row is not None else None
        is_finished = bool(run_row and run_row["finished_at"])

        # Once ``finish_run`` has stamped a terminal state, that stamp
        # is authoritative. The derivation path below is reserved for
        # *unfinished* runs (the SIGKILL recovery case): a process that
        # died never called ``finish_run`` so ``finished_at`` is NULL.
        if is_finished and stamped is not None:
            return stamped

        if not rows:
            # No steps yet, no finish stamp — process is still ramping
            # up (or died before any step).
            return "running"

        committed = [r for r in rows if r["status"] == "committed"]
        failed = [r for r in rows if r["status"] == "failed"]
        unknown = [r for r in rows if r["outcome_ts"] is None]
        rolled = [r for r in rows if r["status"] in {"rolled_back", "rollback_skipped"}]

        if rolled and not committed and not failed and not unknown:
            return "rolled_back"

        if committed and (unknown or failed):
            return "partially_committed"

        if committed and not unknown and not failed:
            return "completed"

        if failed and not committed and not unknown:
            return "failed"

        # Only intent-only rows (the process is mid-run or died before any
        # response came back).  Treat as "running" — operator can decide.
        return "running"

    # ── Class-level scanner ─────────────────────────────────────────

    @classmethod
    def list_runs(
        cls,
        *,
        base_dir: Path | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Scan all ``*.db`` files under ``base_dir`` and return a
        summary row per run, most-recent-first.

        The returned ``state`` is the *derived* state — not the stamp —
        so a SIGKILLed run shows up as ``partially_committed`` instead
        of the stale ``running``.
        """
        d = Path(base_dir) if base_dir is not None else _default_base_dir()
        if not d.exists():
            return []

        rows: list[dict[str, Any]] = []
        for db_file in d.glob("*.db"):
            try:
                ledger = cls(run_id=db_file.stem, base_dir=d)
                run = ledger.get_run(db_file.stem)
                step_count = len(ledger.get_steps(db_file.stem))
                derived = ledger.compute_run_state(db_file.stem)
                rows.append(
                    {
                        "run_id": run["run_id"],
                        "started_at": run["started_at"],
                        "finished_at": run["finished_at"],
                        "profile": run["profile"],
                        "environment": run["environment"],
                        "company": run["company"],
                        "manifest_path": run["manifest_path"],
                        "state": derived,
                        "step_count": step_count,
                    }
                )
                ledger.close()
            except (sqlite3.DatabaseError, LookupError):
                # Skip malformed / unrelated .db files in the dir.
                continue

        if state is not None:
            rows = [r for r in rows if r["state"] == state]
        rows.sort(key=lambda r: r["started_at"] or "", reverse=True)
        return rows[:limit]


# ─── Helpers ─────────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    """UTC timestamp formatted as ISO 8601 with second precision.

    Microsecond precision in tests is fine but creates noisy diffs when
    a human reads the ledger; we keep the historic ``isoformat()``
    output but strip the trailing ``+00:00`` and add ``Z`` to match the
    rest of bcli's audit/telemetry formats.
    """
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
