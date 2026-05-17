"""``bcli batch run`` writes an intent row before each HTTP call and an
outcome row after, surviving SIGKILL in the gap.

These are integration tests against ``run_batch`` — they patch HOME so the
ledger lands in tmp_path, patch ``make_async_client`` to inject a fake
async client, and assert on the resulting SQLite file.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


@pytest.fixture
def fake_client():
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.post = AsyncMock(return_value={"id": "rec-1", "@odata.context": "x"})
    c.patch = AsyncMock(return_value={"id": "rec-1"})
    c.delete = AsyncMock(return_value=None)
    c.get = AsyncMock(
        return_value=type("R", (), {"value": [], "raw": None})()
    )
    # Used so the ledger can compose a rollback URL for POSTs.
    c._resolve_url = lambda entity, record_id=None, **_: (
        f"https://x/{entity}({record_id})" if record_id else f"https://x/{entity}"
    )
    return c


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return f


# ─── Intent before HTTP ──────────────────────────────────────────────


class TestIntentBeforeHttp:
    def test_intent_written_before_post(
        self, writable_state, ledger_home, fake_client, tmp_path,
    ):
        """When the fake client's POST is invoked, an intent row must
        already exist in the ledger DB. The test enforces ordering via a
        side_effect that reads the DB at the moment of the call.
        """
        captured = {}

        async def post_side_effect(entity, body, **kwargs):
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
            captured["body"] = body
            return {"id": "created-1"}

        fake_client.post.side_effect = post_side_effect

        f = _write_yaml(tmp_path, "p.yaml", """
            steps:
              - name: create
                action: post
                endpoint: items
                data:
                  displayName: "ok"
        """)
        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            run_batch(
                file=f, dry_run=False, output=None, format=None,
                set_params=None, params_file=None, yes=True,
            )

        # Post-run: outcome row populated.
        db_files = list(ledger_home.glob("*.db"))
        assert len(db_files) == 1
        with sqlite3.connect(db_files[0]) as conn:
            conn.row_factory = sqlite3.Row
            rows = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM step ORDER BY seq"
                )
            ]
        assert len(rows) == 1
        assert rows[0]["status"] == "committed"
        assert rows[0]["outcome_ts"] is not None
        assert rows[0]["method"] == "POST"
        assert "items" in rows[0]["url"]

    def test_partially_committed_after_simulated_crash(
        self, writable_state, ledger_home, fake_client, tmp_path,
    ):
        """Step 1 succeeds; step 2 'crashes' (raises mid-flight) — the run
        ledger surfaces ``partially_committed`` on read.
        """
        call_count = {"n": 0}

        async def post_side_effect(entity, body, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Simulate SIGKILL between intent + outcome by raising a
                # BaseException that batch_cmd cannot catch with `except
                # Exception:`.
                raise BaseException("simulated SIGKILL")
            return {"id": f"rec-{call_count['n']}"}

        fake_client.post.side_effect = post_side_effect

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
        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            with pytest.raises(BaseException, match="simulated SIGKILL"):
                run_batch(
                    file=f, dry_run=False, output=None, format=None,
                    set_params=None, params_file=None, yes=True,
                )

        # Read the ledger after the crash.
        from bcli.batch.ledger import Ledger

        rows = Ledger.list_runs(base_dir=ledger_home)
        assert len(rows) == 1
        # Either persisted via SIGKILL-safe handler OR derived on read —
        # the contract is "the operator sees partially_committed".
        assert rows[0]["state"] == "partially_committed"

    def test_normal_completion_marks_completed(
        self, writable_state, ledger_home, fake_client, tmp_path,
    ):
        f = _write_yaml(tmp_path, "ok.yaml", """
            steps:
              - name: s1
                action: post
                endpoint: items
                data: {x: 1}
        """)
        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            run_batch(
                file=f, dry_run=False, output=None, format=None,
                set_params=None, params_file=None, yes=True,
            )

        from bcli.batch.ledger import Ledger
        rows = Ledger.list_runs(base_dir=ledger_home)
        assert len(rows) == 1
        assert rows[0]["state"] == "completed"


# ─── Rollback URL capture ────────────────────────────────────────────


class TestRollbackUrlCapture:
    def test_post_step_stores_rollback_url(
        self, writable_state, ledger_home, fake_client, tmp_path,
    ):
        """POST that returns an ``id`` → rollback_url = entity_url(id)."""
        fake_client.post.return_value = {"id": "VND-1"}
        f = _write_yaml(tmp_path, "p.yaml", """
            steps:
              - name: create_vendor
                action: post
                endpoint: vendors
                data: {name: "AAR"}
        """)
        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            run_batch(
                file=f, dry_run=False, output=None, format=None,
                set_params=None, params_file=None, yes=True,
            )

        db_files = list(ledger_home.glob("*.db"))
        with sqlite3.connect(db_files[0]) as conn:
            conn.row_factory = sqlite3.Row
            row = dict(
                conn.execute("SELECT * FROM step LIMIT 1").fetchone()
            )
        assert row["rollback_url"]
        assert "vendors" in row["rollback_url"]
        assert "VND-1" in row["rollback_url"]
