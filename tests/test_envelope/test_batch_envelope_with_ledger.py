"""Cross-cutting integration: ``bcli batch run`` must keep the envelope
(#15) and the SQLite ledger (#16) in agreement.

Three scenarios:

1. **Happy path.** Mutating batch on a writable profile → envelope
   ``status="succeeded"``, ledger ``state="completed"``, envelope
   ``record_id`` equals the ledger ``run_id``.
2. **Partial commit.** One step succeeds, the next raises → envelope
   ``status="failed"``, ledger ``state="partially_committed"``.
3. **Policy refusal.** ``disable_writes=true`` + non-interactive +
   no ``--yes`` → envelope ``status="failed"`` with ``exit_code=1``;
   the ledger row exists (``start_run`` ran) and is finalized as
   ``failed`` (no committed steps).

The first two are the real meat of the integration — they pin the
ledger/envelope coupling the lead asked about. The policy-refusal test
documents the chosen behavior so it doesn't regress.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import typer

from bcli.batch.ledger import Ledger
from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.commands import batch_cmd


# ── Helpers ────────────────────────────────────────────────────────────


def _writable_cfg() -> BCConfig:
    return BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={
            "dev": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                company_id="c-123",
                disable_writes=False,
            ),
        },
    )


def _readonly_cfg() -> BCConfig:
    return BCConfig(
        defaults=BCDefaults(profile="prod"),
        profiles={
            "prod": BCProfile(
                tenant_id="t1",
                environment="Production",
                company_id="c-999",
                disable_writes=True,
            ),
        },
    )


@pytest.fixture
def writable_state():
    state._config = _writable_cfg()
    state._registry = None
    state._telemetry = None
    state.profile_name = None
    state.dry_run = False
    state.quiet = True
    yield
    state._config = None
    state._registry = None
    state._telemetry = None


@pytest.fixture
def readonly_state():
    state._config = _readonly_cfg()
    state._registry = None
    state._telemetry = None
    state.profile_name = None
    state.dry_run = False
    state.quiet = True
    yield
    state._config = None
    state._registry = None
    state._telemetry = None


@pytest.fixture
def fake_client():
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.post = AsyncMock(return_value={"id": "rec-1", "@odata.context": "x"})
    c.patch = AsyncMock(return_value={"id": "rec-1"})
    c.delete = AsyncMock(return_value=None)
    c.get = AsyncMock(return_value=type("R", (), {"value": [], "raw": None})())
    c._resolve_url = lambda entity, record_id=None, **_: (
        f"https://x/{entity}({record_id})" if record_id else f"https://x/{entity}"
    )
    return c


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return f


def _read_ledger_run(ledger_dir: Path) -> dict:
    """Return the single run row in ``ledger_dir`` (asserts there is one)."""
    db_files = list(ledger_dir.glob("*.db"))
    assert len(db_files) == 1, (
        f"Expected exactly one ledger DB in {ledger_dir}, got {db_files}"
    )
    with sqlite3.connect(db_files[0]) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM run").fetchone()
    return dict(row)


# ── Tests ──────────────────────────────────────────────────────────────


class TestEnvelopeAndLedgerAgreeOnSuccess:
    """Happy path: both attestations report a clean run, and the envelope's
    ``record_id`` is the ledger's ``run_id`` (the cross-reference an agent
    uses to drill into per-step detail)."""

    def test_success_envelope_matches_ledger_run_id(
        self, writable_state, isolated_home, fake_client, tmp_path,
    ):
        yaml_file = _write_yaml(tmp_path, "ok.yaml", """
            name: happy
            steps:
              - name: create_vendor
                action: post
                endpoint: vendors
                data: {displayName: Acme}
        """)
        out = tmp_path / "envelope.json"

        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            batch_cmd.run_batch(
                file=yaml_file, dry_run=False, output=None, format=None,
                set_params=None, params_file=None, yes=True,
                result_out=out, result_fd=None,
            )

        env = json.loads(out.read_text())
        assert env["status"] == "succeeded"
        assert env["exit_code"] == 0
        assert env["method"] == "BATCH_RUN"

        run_row = _read_ledger_run(isolated_home)
        # Derived state via Ledger.list_runs (computed, not the raw state col).
        derived = Ledger.list_runs(base_dir=isolated_home)[0]
        assert derived["state"] == "completed"
        # The envelope's record_id is the ledger run id — agents pivot here.
        assert env["record_id"] == run_row["run_id"]


class TestEnvelopeAndLedgerAgreeOnPartialCommit:
    """Step 1 commits, step 2 explodes mid-run. Envelope marks failed;
    ledger derives ``partially_committed`` from the step rows that
    landed before the crash."""

    def test_partial_commit_envelope_failed_ledger_partial(
        self, writable_state, isolated_home, fake_client, tmp_path,
    ):
        call_count = {"n": 0}

        async def post_side_effect(entity, body, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("boom on step 2")
            return {"id": f"rec-{call_count['n']}"}

        fake_client.post.side_effect = post_side_effect

        yaml_file = _write_yaml(tmp_path, "partial.yaml", """
            name: partial
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
        out = tmp_path / "envelope.json"

        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            with pytest.raises(typer.Exit) as excinfo:
                batch_cmd.run_batch(
                    file=yaml_file, dry_run=False, output=None, format=None,
                    set_params=None, params_file=None, yes=True,
                    result_out=out, result_fd=None,
                )
        assert excinfo.value.exit_code == 1

        env = json.loads(out.read_text())
        assert env["status"] == "failed"
        assert env["exit_code"] == 1

        # Ledger derived state must reflect "one in, one out" → partial.
        derived = Ledger.list_runs(base_dir=isolated_home)[0]
        assert derived["state"] == "partially_committed", derived
        # Cross-reference still holds even on failure.
        assert env["record_id"] == derived["run_id"]


class TestEnvelopeOnPolicyRefusalForBatch:
    """Read-only profile + non-interactive + no --yes refuses the batch.

    Chosen behavior: the ledger row exists (``start_run`` ran before the
    gate, so an audit trail of "the run was attempted" is preserved),
    finalized as ``failed``. The envelope is ``status="failed"``,
    ``exit_code=1``. No ``_execute_batch`` call, no step rows.
    """

    def test_policy_refusal_emits_failed_envelope_and_failed_ledger(
        self, readonly_state, isolated_home, fake_client, tmp_path, monkeypatch,
    ):
        # Force non-interactive stdin so confirm_write_or_exit refuses.
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        yaml_file = _write_yaml(tmp_path, "blocked.yaml", """
            name: blocked
            steps:
              - name: s1
                action: post
                endpoint: items
                data: {x: 1}
        """)
        out = tmp_path / "envelope.json"

        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            with pytest.raises(typer.Exit) as excinfo:
                batch_cmd.run_batch(
                    file=yaml_file, dry_run=False, output=None, format=None,
                    set_params=None, params_file=None, yes=False,
                    result_out=out, result_fd=None,
                )
        assert excinfo.value.exit_code == 1

        env = json.loads(out.read_text())
        assert env["status"] == "failed"
        assert env["exit_code"] == 1
        assert env["profile"] == "prod"
        assert env["environment"] == "Production"

        # Ledger row exists (start_run ran), finalized as failed.
        derived = Ledger.list_runs(base_dir=isolated_home)[0]
        assert derived["state"] == "failed", derived
        assert env["record_id"] == derived["run_id"]
        # No step row should have been written — the gate fired before
        # _execute_batch.
        with sqlite3.connect(next(iter(isolated_home.glob("*.db")))) as conn:
            conn.row_factory = sqlite3.Row
            steps = conn.execute("SELECT * FROM step").fetchall()
        assert steps == []

        # And the actual write was never issued.
        fake_client.post.assert_not_awaited()
