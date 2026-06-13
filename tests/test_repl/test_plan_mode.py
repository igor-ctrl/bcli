"""Plan-mode draft → file round-trip and batch-run invocation."""

from __future__ import annotations

from pathlib import Path

import yaml

from bcli_cli.repl import _plan_mode


def test_write_draft_round_trips_yaml(tmp_path, monkeypatch) -> None:
    doc = {
        "name": "onboard",
        "steps": [
            {"name": "create_vendor", "action": "post", "endpoint": "vendors",
             "data": {"displayName": "Acme"}},
        ],
    }
    yaml_text = yaml.safe_dump(doc, sort_keys=False)
    path = _plan_mode.write_draft(yaml_text, name="onboard vendor!")
    assert path.exists()
    assert path.suffix == ".yaml"
    # Sanitised, recognisable filename.
    assert "onboard" in path.name
    reloaded = yaml.safe_load(path.read_text())
    assert reloaded == doc


async def test_run_batch_builds_correct_argv(monkeypatch) -> None:
    captured = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b'{"ok": true}', b"")

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = list(argv)
        return FakeProc()

    monkeypatch.setattr(_plan_mode.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(_plan_mode.shutil, "which", lambda _x: "/usr/bin/bcli")

    ok, out = await _plan_mode.run_batch(
        Path("/tmp/x.batch.yaml"), profile_name="sandbox", dry_run=True,
    )
    assert ok is True
    argv = captured["argv"]
    assert argv[0] == "bcli"
    assert "--profile" in argv and "sandbox" in argv
    assert "batch" in argv and "run" in argv
    assert "--dry-run" in argv
    assert "--yes" not in argv  # dry-run never auto-approves a write


async def test_run_batch_real_run_passes_yes(monkeypatch) -> None:
    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"done", b"")

    captured = {}

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = list(argv)
        return FakeProc()

    monkeypatch.setattr(_plan_mode.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(_plan_mode.shutil, "which", lambda _x: "/usr/bin/bcli")

    ok, _ = await _plan_mode.run_batch(Path("/tmp/x.yaml"), dry_run=False)
    assert ok is True
    assert "--yes" in captured["argv"]
    assert "--dry-run" not in captured["argv"]


async def test_run_batch_reports_failure(monkeypatch) -> None:
    class FakeProc:
        returncode = 2

        async def communicate(self):
            return (b"", b"boom")

    async def fake_exec(*argv, **kwargs):
        return FakeProc()

    monkeypatch.setattr(_plan_mode.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(_plan_mode.shutil, "which", lambda _x: "/usr/bin/bcli")

    ok, out = await _plan_mode.run_batch(Path("/tmp/x.yaml"))
    assert ok is False
    assert "boom" in out
