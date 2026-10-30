"""Regression tests for vuln-0002 — batch write-gate enforcement.

Before the fix, ``bcli batch run`` happily executed YAML steps with
mutating actions (post/patch/delete) on profiles marked
``disable_writes = true``, even though the equivalent direct commands
(``bcli post``, ``bcli patch``, ``bcli delete``) refused to write under
the same conditions. The CLI's read-only safety guarantee was inconsistent
across command paths — that's the alternate-path bypass strix flagged.

These tests assert that:
  * a non-interactive batch with a mutating step on a read-only profile
    aborts with ``typer.Exit(8)`` and sends nothing to BC;
  * the same batch with ``--yes`` proceeds (scripted use);
  * a batch composed exclusively of GET steps is unaffected by the
    disable_writes flag (read-only is fine on read-only profile);
  * a writable profile is unaffected.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import typer

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.commands.batch_cmd import run_batch


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return f


def _readonly_config() -> BCConfig:
    return BCConfig(
        defaults=BCDefaults(profile="readonly"),
        profiles={
            "readonly": BCProfile(
                tenant_id="t1",
                environment="Production",
                company_id="c-1",
                disable_writes=True,
            ),
        },
    )


def _writable_config() -> BCConfig:
    return BCConfig(
        defaults=BCDefaults(profile="writable"),
        profiles={
            "writable": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                company_id="c-1",
                disable_writes=False,
            ),
        },
    )


@pytest.fixture
def readonly_state():
    """Active profile is read-only; non-interactive (TTY-less) by default."""
    state._config = _readonly_config()
    state._registry = None
    state.profile_name = None
    state.dry_run = False
    state.quiet = True
    yield
    state._config = None
    state._registry = None


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
def non_interactive(monkeypatch):
    """Pretend stdin is not a TTY — matches CI/automation."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    yield


@pytest.fixture(autouse=True)
def _isolate_ledger_home(tmp_path, monkeypatch):
    """Phase 3 ledger writes ~/.config/bcli/batch/<run-id>.db on every
    ``run_batch`` invocation. These safety tests pre-date the ledger and
    don't care about its output — but they MUST NOT pollute the
    developer's real home dir. Redirect Path.home() to tmp_path so the
    ledger files land in a tear-down-friendly location.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield


# ── Read-only profile blocks mutating batches ────────────────────────────


class TestReadonlyProfileBlocksMutatingSteps:
    def test_post_step_aborts_without_yes(
        self, readonly_state, non_interactive, fake_bc, tmp_path,
    ):
        f = _write_yaml(tmp_path, "post.yaml", """
            name: bypass-post
            steps:
              - name: create_item
                action: post
                endpoint: items
                data:
                  displayName: "batch write"
        """)
        with pytest.raises(typer.Exit) as excinfo:
            run_batch(
                file=f, dry_run=False, output=None, format=None,
                set_params=None, params_file=None, yes=False,
            )
        assert excinfo.value.exit_code == 8  # AIP §Phase 4a: EXIT_POLICY
        assert fake_bc.sent == []

    def test_patch_step_aborts_without_yes(
        self, readonly_state, non_interactive, fake_bc, tmp_path,
    ):
        f = _write_yaml(tmp_path, "patch.yaml", """
            name: bypass-patch
            steps:
              - name: patch_item
                action: patch
                endpoint: items
                id: "record-1"
                etag: "*"
                data:
                  displayName: "batch patch"
        """)
        with pytest.raises(typer.Exit):
            run_batch(
                file=f, dry_run=False, output=None, format=None,
                set_params=None, params_file=None, yes=False,
            )
        assert fake_bc.sent == []

    def test_delete_step_aborts_without_yes(
        self, readonly_state, non_interactive, fake_bc, tmp_path,
    ):
        f = _write_yaml(tmp_path, "delete.yaml", """
            name: bypass-delete
            steps:
              - name: delete_item
                action: delete
                endpoint: items
                id: "record-2"
                etag: "*"
        """)
        with pytest.raises(typer.Exit):
            run_batch(
                file=f, dry_run=False, output=None, format=None,
                set_params=None, params_file=None, yes=False,
            )
        assert fake_bc.sent == []

    def test_mixed_steps_with_one_mutation_aborts(
        self, readonly_state, non_interactive, fake_bc, tmp_path,
    ):
        """A single mutating step among many gets caught by the gate."""
        f = _write_yaml(tmp_path, "mixed.yaml", """
            steps:
              - name: read_one
                action: get
                endpoint: items
              - name: read_two
                action: get
                endpoint: vendors
              - name: do_write
                action: post
                endpoint: items
                data:
                  displayName: "batch write"
        """)
        with pytest.raises(typer.Exit):
            run_batch(
                file=f, dry_run=False, output=None, format=None,
                set_params=None, params_file=None, yes=False,
            )
        # No GET happens either — we abort BEFORE any step runs, so the
        # whole batch is refused atomically.
        assert fake_bc.sent == []


# ── --yes overrides the gate (scripted use) ──────────────────────────────


class TestReadonlyProfileWithYesProceeds:
    def test_yes_flag_lets_mutating_batch_proceed(
        self, readonly_state, non_interactive, fake_bc, tmp_path,
    ):
        f = _write_yaml(tmp_path, "post.yaml", """
            steps:
              - name: create_item
                action: post
                endpoint: items
                data:
                  displayName: "batch write"
        """)
        run_batch(
            file=f, dry_run=False, output=None, format=None,
            set_params=None, params_file=None, yes=True,
        )
        assert [s.method for s in fake_bc.sent] == ["POST"]


# ── Read-only batches are unaffected ─────────────────────────────────────


class TestReadOnlyBatchUnaffected:
    def test_get_only_batch_runs_on_readonly_profile(
        self, readonly_state, non_interactive, fake_bc, tmp_path,
    ):
        """A GET-only batch must still work on a disable_writes profile —
        otherwise read-only profiles become useless for read-heavy automation.
        """
        f = _write_yaml(tmp_path, "get.yaml", """
            steps:
              - name: read_items
                action: get
                endpoint: items
              - name: read_vendors
                action: get
                endpoint: vendors
        """)
        fake_bc.default = {"value": []}
        run_batch(
            file=f, dry_run=False, output=None, format=None,
            set_params=None, params_file=None, yes=False,
        )
        # Both GET calls executed, no prompt/abort.
        assert [s.method for s in fake_bc.sent] == ["GET", "GET"]


# ── Writable profile is unaffected ───────────────────────────────────────


class TestWritableProfileUnaffected:
    def test_mutating_batch_runs_on_writable_profile(
        self, writable_state, non_interactive, fake_bc, tmp_path,
    ):
        f = _write_yaml(tmp_path, "post.yaml", """
            steps:
              - name: create_item
                action: post
                endpoint: items
                data:
                  displayName: "ok"
        """)
        run_batch(
            file=f, dry_run=False, output=None, format=None,
            set_params=None, params_file=None, yes=False,
        )
        assert [s.method for s in fake_bc.sent] == ["POST"]


# ── Dry-run skips both execution and the gate ────────────────────────────


class TestDryRunSkipsGate:
    def test_dry_run_does_not_prompt_or_abort(
        self, readonly_state, non_interactive, fake_bc, tmp_path,
    ):
        """``--dry-run`` already short-circuits before any execution,
        so the gate must not trigger either — otherwise read-only users
        couldn't preview a workflow without reaching for ``--yes``.
        """
        f = _write_yaml(tmp_path, "post.yaml", """
            steps:
              - name: create_item
                action: post
                endpoint: items
                data:
                  displayName: "preview"
        """)
        run_batch(
            file=f, dry_run=True, output=None, format=None,
            set_params=None, params_file=None, yes=False,
        )
        assert fake_bc.sent == []
