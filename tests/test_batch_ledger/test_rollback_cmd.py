"""``bcli batch rollback <run-id>`` issues inverse operations for committed
POST steps and refuses to touch PATCH/DELETE without manual cleanup."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from bcli.batch.ledger import Ledger
from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.app import app


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def ledger_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path / ".config" / "bcli" / "batch"


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


def _readonly_config() -> BCConfig:
    return BCConfig(
        defaults=BCDefaults(profile="ro"),
        profiles={
            "ro": BCProfile(
                tenant_id="t1",
                environment="Production",
                company_id="c-1",
                disable_writes=True,
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
def readonly_state():
    state._config = _readonly_config()
    state._registry = None
    state.profile_name = None
    state.dry_run = False
    state.quiet = True
    yield
    state._config = None
    state._registry = None


@pytest.fixture
def fake_client():
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.delete = AsyncMock(return_value=None)
    c.delete_url = AsyncMock(return_value=None)
    return c


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_committed_post(base_dir: Path, run_id: str, *, rollback_url="https://x/items(rec-1)"):
    """Seed a ledger with one committed POST whose rollback_url is known."""
    ledger = Ledger(run_id=run_id, base_dir=base_dir)
    ledger.start_run(
        manifest_path=f"/wf/{run_id}.yaml", manifest_hash="hh",
        profile="dev", environment="Sandbox", company="c-1",
    )
    sid = ledger.write_intent(seq=1, method="POST", url="https://x/items", body_hash="bh")
    ledger.write_outcome(
        step_id=sid, status="committed",
        bc_correlation_id="corr-1", error_message=None,
        rollback_url=rollback_url,
    )
    ledger.finish_run(run_id, "completed")
    ledger.close()  # avoid holding the WAL while another Ledger opens it
    return ledger


# ─── POST rollback ───────────────────────────────────────────────────


class TestPostRollback:
    def test_committed_post_issues_delete(
        self, writable_state, ledger_home, fake_client, runner,
    ):
        _seed_committed_post(ledger_home, "rb-1")

        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            result = runner.invoke(app, ["batch", "rollback", "rb-1", "--yes"])

        assert result.exit_code == 0, result.output
        # The fake client's delete_url (or delete) was invoked with the
        # stored rollback_url.
        called = (
            fake_client.delete_url.await_count + fake_client.delete.await_count
        )
        assert called == 1

        # Step state updated to rolled_back; run state too.
        from bcli.batch.ledger import Ledger as L

        rows = L.list_runs(base_dir=ledger_home)
        assert rows[0]["state"] == "rolled_back"

    def test_rollback_dry_run_does_not_call_delete(
        self, writable_state, ledger_home, fake_client, runner,
    ):
        _seed_committed_post(ledger_home, "rb-dry")

        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            result = runner.invoke(
                app, ["batch", "rollback", "rb-dry", "--dry-run", "--yes"],
            )
        assert result.exit_code == 0, result.output
        fake_client.delete_url.assert_not_awaited()
        fake_client.delete.assert_not_awaited()


# ─── PATCH / DELETE skip ─────────────────────────────────────────────


class TestPatchDeleteSkipped:
    def _seed_committed_patch(self, base_dir, run_id):
        ledger = Ledger(run_id=run_id, base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="",
            profile="dev", environment="Sandbox", company="c-1",
        )
        sid = ledger.write_intent(
            seq=1, method="PATCH", url="https://x/items(rec-1)", body_hash="bh",
        )
        ledger.write_outcome(
            step_id=sid, status="committed",
            bc_correlation_id="corr-1", error_message=None,
            rollback_url=None,
        )
        ledger.finish_run(run_id, "completed")
        ledger.close()

    def _seed_committed_delete(self, base_dir, run_id):
        ledger = Ledger(run_id=run_id, base_dir=base_dir)
        ledger.start_run(
            manifest_path="", manifest_hash="",
            profile="dev", environment="Sandbox", company="c-1",
        )
        sid = ledger.write_intent(
            seq=1, method="DELETE", url="https://x/items(rec-1)", body_hash="bh",
        )
        ledger.write_outcome(
            step_id=sid, status="committed",
            bc_correlation_id="corr-1", error_message=None,
            rollback_url=None,
        )
        ledger.finish_run(run_id, "completed")
        ledger.close()

    def test_patch_step_is_rollback_skipped(
        self, writable_state, ledger_home, fake_client, runner,
    ):
        self._seed_committed_patch(ledger_home, "rb-patch")
        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            result = runner.invoke(app, ["batch", "rollback", "rb-patch", "--yes"])
        assert result.exit_code == 0, result.output
        fake_client.delete_url.assert_not_awaited()
        fake_client.delete.assert_not_awaited()

        # Step status flipped to rollback_skipped.
        db_files = list(ledger_home.glob("*.db"))
        with sqlite3.connect(db_files[0]) as conn:
            conn.row_factory = sqlite3.Row
            (status,) = conn.execute(
                "SELECT status FROM step LIMIT 1"
            ).fetchone()
        assert status == "rollback_skipped"

    def test_delete_step_is_rollback_skipped(
        self, writable_state, ledger_home, fake_client, runner,
    ):
        self._seed_committed_delete(ledger_home, "rb-del")
        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            result = runner.invoke(app, ["batch", "rollback", "rb-del", "--yes"])
        assert result.exit_code == 0
        fake_client.delete_url.assert_not_awaited()
        fake_client.delete.assert_not_awaited()


# ─── disable_writes refusal ──────────────────────────────────────────


class TestDisableWritesBlocksRollback:
    def test_readonly_profile_aborts_before_any_http(
        self, readonly_state, ledger_home, fake_client, runner,
    ):
        _seed_committed_post(ledger_home, "rb-ro")

        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            # --yes does NOT bypass disable_writes on rollback; refusal
            # is unconditional. We invoke without --yes and assert
            # non-zero exit.
            result = runner.invoke(app, ["batch", "rollback", "rb-ro"])

        assert result.exit_code != 0
        fake_client.delete_url.assert_not_awaited()
        fake_client.delete.assert_not_awaited()


# ─── Unknown (intent-only) steps skipped ─────────────────────────────


class TestUnknownStepsSkipped:
    def test_intent_only_step_is_not_rolled_back(
        self, writable_state, ledger_home, fake_client, runner,
    ):
        """An intent row with no outcome (e.g. the process died between
        intent and outcome) must not be 'rolled back' — we don't know
        whether the HTTP succeeded server-side.
        """
        ledger = Ledger(run_id="rb-unk", base_dir=ledger_home)
        ledger.start_run(
            manifest_path="", manifest_hash="",
            profile="dev", environment="Sandbox", company="c-1",
        )
        sid_committed = ledger.write_intent(
            seq=1, method="POST", url="https://x/items", body_hash="bh",
        )
        ledger.write_outcome(
            step_id=sid_committed, status="committed",
            bc_correlation_id="c1", error_message=None,
            rollback_url="https://x/items(rec-1)",
        )
        # Step 2: intent only.
        ledger.write_intent(
            seq=2, method="POST", url="https://x/items", body_hash="bh2",
        )
        ledger.close()

        with patch(
            "bcli_cli.commands.batch_cmd.state.make_async_client",
            return_value=fake_client,
        ):
            result = runner.invoke(app, ["batch", "rollback", "rb-unk", "--yes"])

        assert result.exit_code == 0, result.output
        # Exactly one rollback DELETE, for the committed step. The
        # intent-only step is left alone.
        called = (
            fake_client.delete_url.await_count + fake_client.delete.await_count
        )
        assert called == 1
