"""Same-run idempotency replay protection — integration tests.

PR #18 review: ``Ledger.find_committed_idempotent_step`` was a primitive
without production call sites. This module covers the wiring through
``bcli batch run``:

* Two mutating steps with the same ``idempotency_key`` inside one
  ``batch run`` invocation → the second is **replayed** (no HTTP fired,
  no duplicate ledger row, ``status="replayed"`` on the result entry).
* Different ``idempotency_key`` values do NOT collide.
* No ``idempotency_key`` on either step is the existing behavior — both
  fire HTTP independently.

Cross-run replay is explicitly out of scope for v0.1 (see PR body).
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.commands.batch_cmd import run_batch


# ─── Fixtures (mirror tests/test_batch_ledger/test_batch_cmd_ledger.py) ─


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
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path / ".config" / "bcli" / "batch"


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return f


# ─── Same-run replay ────────────────────────────────────────────────


class TestSameRunIdempotencyReplay:
    """Two steps with the same ``idempotency_key`` in one batch run.

    The second step must be replayed: no second HTTP call, no second
    ``step`` row with that key in ``committed`` status, ``replayed=True``
    on the result entry.
    """

    def test_duplicate_key_replays_second_step(
        self, writable_state, ledger_home, fake_bc, tmp_path,
    ):
        yaml_file = _write_yaml(tmp_path, "dup.yaml", """
            name: dup-idempotent
            steps:
              - name: s1
                action: post
                endpoint: vendors
                data: {displayName: "Acme"}
                idempotency_key: op-shared
              - name: s2
                action: post
                endpoint: vendors
                data: {displayName: "Acme again"}
                idempotency_key: op-shared
        """)

        run_batch(
            file=yaml_file, dry_run=False, output=None, format=None,
            set_params=None, params_file=None, yes=False,
        )

        # Exactly one POST was sent (the first step), carrying the key as
        # the Idempotency-Key header. The second step short-circuited on the
        # replay path.
        assert [(s.method, s.idempotency_key) for s in fake_bc.sent] == [
            ("POST", "op-shared"),
        ], "second step must replay (no second POST)"

        # The first step's idempotency_key landed in the ledger and was
        # committed; the second step's intent row does NOT exist.
        dbs = list(ledger_home.glob("*.db"))
        assert len(dbs) == 1, dbs
        with sqlite3.connect(dbs[0]) as conn:
            rows = conn.execute(
                "SELECT seq, status, idempotency_key FROM step "
                "WHERE idempotency_key = ? ORDER BY seq",
                ("op-shared",),
            ).fetchall()
        # Only the first step's row is persisted; the replayed second
        # step does NOT write a duplicate intent.
        assert rows == [(1, "committed", "op-shared")], rows

    def test_distinct_keys_both_fire_http(
        self, writable_state, ledger_home, fake_bc, tmp_path,
    ):
        """Sanity: different keys don't trigger replay."""
        yaml_file = _write_yaml(tmp_path, "two-keys.yaml", """
            name: two-distinct-keys
            steps:
              - name: s1
                action: post
                endpoint: vendors
                data: {displayName: "Acme"}
                idempotency_key: op-A
              - name: s2
                action: post
                endpoint: vendors
                data: {displayName: "Beta"}
                idempotency_key: op-B
        """)

        run_batch(
            file=yaml_file, dry_run=False, output=None, format=None,
            set_params=None, params_file=None, yes=False,
        )

        assert [s.idempotency_key for s in fake_bc.sent] == ["op-A", "op-B"], (
            "distinct keys must not trigger replay"
        )

        dbs = list(ledger_home.glob("*.db"))
        with sqlite3.connect(dbs[0]) as conn:
            rows = conn.execute(
                "SELECT seq, status, idempotency_key FROM step ORDER BY seq",
            ).fetchall()
        assert rows == [
            (1, "committed", "op-A"),
            (2, "committed", "op-B"),
        ], rows

    def test_no_key_no_replay(
        self, writable_state, ledger_home, fake_bc, tmp_path,
    ):
        """Steps without an idempotency_key keep existing behavior:
        every step fires HTTP and lands an intent + outcome row."""
        yaml_file = _write_yaml(tmp_path, "no-keys.yaml", """
            name: no-keys
            steps:
              - name: s1
                action: post
                endpoint: vendors
                data: {displayName: "Acme"}
              - name: s2
                action: post
                endpoint: vendors
                data: {displayName: "Beta"}
        """)

        run_batch(
            file=yaml_file, dry_run=False, output=None, format=None,
            set_params=None, params_file=None, yes=False,
        )

        assert [s.method for s in fake_bc.sent] == ["POST", "POST"]

        dbs = list(ledger_home.glob("*.db"))
        with sqlite3.connect(dbs[0]) as conn:
            rows = conn.execute(
                "SELECT seq, status, idempotency_key FROM step ORDER BY seq",
            ).fetchall()
        assert rows == [
            (1, "committed", None),
            (2, "committed", None),
        ], rows
