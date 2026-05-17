"""``bcli batch state`` and ``bcli batch list`` commands.

These talk to the same ledger files written by ``batch run``.  We seed the
ledger by hand (rather than running a whole batch) so the tests focus on
the read-side commands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bcli.batch.ledger import Ledger
from bcli_cli.app import app
from bcli_cli._state import state


@pytest.fixture
def ledger_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path / ".config" / "bcli" / "batch"


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    state._config = None
    state._registry = None
    state.profile_name = None
    state.dry_run = False
    state.quiet = True
    yield


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_run(base_dir: Path, run_id: str, *, with_step=True, finish="completed"):
    """Helper: create a ledger file with a single committed step."""
    ledger = Ledger(run_id=run_id, base_dir=base_dir)
    ledger.start_run(
        manifest_path=f"/wf/{run_id}.yaml", manifest_hash="hh",
        profile="dev", environment="Sandbox", company="c-1",
    )
    if with_step:
        sid = ledger.write_intent(seq=1, method="POST", url="https://x/items", body_hash="bh")
        ledger.write_outcome(
            step_id=sid, status="committed",
            bc_correlation_id="corr-1", error_message=None,
            rollback_url="https://x/items(rec-1)",
        )
    if finish:
        ledger.finish_run(run_id, finish)
    ledger.close()
    return ledger


# ─── bcli batch state ────────────────────────────────────────────────


class TestBatchState:
    def test_state_json_returns_full_ledger(self, ledger_home, runner):
        _seed_run(ledger_home, "alpha")
        result = runner.invoke(app, ["batch", "state", "alpha", "--format", "json"])
        assert result.exit_code == 0, result.output

        # The JSON payload is written to stdout. The state command sets
        # state.quiet = True for json output, so no banner pollution.
        payload = json.loads(result.stdout)
        assert payload["run"]["run_id"] == "alpha"
        assert payload["run"]["state"] == "completed"
        assert len(payload["steps"]) == 1
        assert payload["steps"][0]["status"] == "committed"
        assert payload["steps"][0]["method"] == "POST"

    def test_state_unknown_run_id_errors(self, ledger_home, runner):
        result = runner.invoke(app, ["batch", "state", "nope", "--format", "json"])
        assert result.exit_code != 0

    def test_state_table_renders_a_summary(self, ledger_home, runner):
        _seed_run(ledger_home, "table-run")
        result = runner.invoke(app, ["batch", "state", "table-run", "--format", "table"])
        assert result.exit_code == 0, result.output
        # The Rich table includes the run_id and a step row.
        assert "table-run" in result.stdout
        assert "POST" in result.stdout


# ─── bcli batch list ─────────────────────────────────────────────────


class TestBatchList:
    def test_list_returns_recent_runs(self, ledger_home, runner):
        _seed_run(ledger_home, "r1", finish="completed")
        _seed_run(ledger_home, "r2", finish="failed")

        result = runner.invoke(app, ["batch", "list", "--format", "json"])
        assert result.exit_code == 0, result.output

        rows = json.loads(result.stdout)
        ids = {row["run_id"] for row in rows}
        assert ids == {"r1", "r2"}

    def test_list_filter_by_state(self, ledger_home, runner):
        _seed_run(ledger_home, "r-ok", finish="completed")
        _seed_run(ledger_home, "r-bad", finish="failed")

        result = runner.invoke(
            app, ["batch", "list", "--state", "failed", "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)
        assert [r["run_id"] for r in rows] == ["r-bad"]

    def test_list_empty(self, ledger_home, runner):
        ledger_home.mkdir(parents=True, exist_ok=True)
        result = runner.invoke(app, ["batch", "list", "--format", "json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []
