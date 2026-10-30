"""Batch dry-run and rollback go through the SDK write operation.

Dry-run plans each write step exactly as the real run would (URL shown, a
``dry_run`` audit entry, nothing sent); rollback's DELETE is audited like any
other write.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from bcli.config._model import AuditConfig, BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.commands.batch_cmd import rollback_cmd, run_batch


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "audit.jsonl"
    state._config = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={"dev": BCProfile(tenant_id="t1", environment="Sandbox", company_id="c-1")},
        audit=AuditConfig(enabled=True, path=str(path)),
    )
    state._registry = None
    state.profile_name = None
    state.dry_run = False
    state.quiet = True
    yield path
    state._config = None
    state._registry = None


def _entries(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _batch(tmp_path: Path, steps: str) -> Path:
    f = tmp_path / "b.yaml"
    f.write_text("name: t\nsteps:\n" + textwrap.indent(textwrap.dedent(steps).strip(), "  "))
    return f


def _run(f: Path, *, dry_run: bool = False) -> None:
    run_batch(file=f, dry_run=dry_run, output=None, format=None,
              set_params=None, params_file=None, yes=True)


def test_dry_run_plans_writes_and_sends_nothing(audit_path, fake_bc, tmp_path, capsys):
    f = _batch(tmp_path, """
        - action: post
          endpoint: items
          data: {displayName: x}
        - action: patch
          endpoint: items
          data: {displayName: y}
    """)
    _run(f, dry_run=True)

    assert fake_bc.sent == []
    out = capsys.readouterr().out
    assert "URL:" in out and "/companies(c-1)/items" in out.replace("\n", "")
    assert "missing 'id' field" in out
    assert [e["outcome"] for e in _entries(audit_path)] == ["dry_run"]


def test_rollback_delete_is_audited(audit_path, fake_bc, tmp_path):
    fake_bc.reply({"id": "rec-1"})
    _run(_batch(tmp_path, """
        - action: post
          endpoint: items
          data: {displayName: x}
    """))
    run_id = next((tmp_path / ".config" / "bcli" / "batch").glob("*.db")).stem

    rollback_cmd(run_id=run_id, dry_run=False, yes=True)

    assert fake_bc.last.method == "DELETE"
    assert fake_bc.last.url.endswith("/items(rec-1)")
    assert [(e["method"], e["outcome"]) for e in _entries(audit_path)] == [
        ("POST", "completed"), ("DELETE", "completed"),
    ]
