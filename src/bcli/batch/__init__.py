"""Batch operation ledger (Phase 3 of AIP v0.1).

A durable SQLite ledger that records every batch run's intent + outcome
per step.  Survives ``SIGKILL`` because the intent row is written
*before* the HTTP call, and ``PRAGMA synchronous=NORMAL`` keeps WAL
honest at commit time.
"""

from bcli.batch.ledger import Ledger, RunLedgerExistsError  # noqa: F401
