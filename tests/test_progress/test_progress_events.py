"""``--progress-fd N`` event stream contract (AIP §Phase 4e).

For long-running ``bcli batch run`` / ``bcli extract run`` work, agents
want per-step structured events on a dedicated file descriptor:

    {"event": "step_started",   "seq": 3, "method": "POST", ...}
    {"event": "step_completed", "seq": 3, "status": "committed", ...}

These tests pin the event shape via the small ``ProgressEmitter`` helper.
End-to-end wiring through ``batch_cmd.run_batch`` is exercised via a
fake fd in ``test_batch_progress_fd``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bcli_cli._progress import ProgressEmitter, _is_real_value


# ─── Emitter primitives ──────────────────────────────────────────────


def test_emitter_writes_one_json_line_per_call(tmp_path: Path):
    out = tmp_path / "events.jsonl"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    emitter = ProgressEmitter(fd=fd)
    emitter.emit(event="step_started", seq=1, method="POST", endpoint="vendors")
    emitter.emit(event="step_completed", seq=1, status="committed",
                  bc_correlation_id="corr-1", duration_ms=287)
    emitter.close()

    lines = out.read_text().splitlines()
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    e2 = json.loads(lines[1])
    assert e1["event"] == "step_started"
    assert e1["seq"] == 1
    assert e1["method"] == "POST"
    assert e1["endpoint"] == "vendors"
    assert "ts" in e1  # timestamp injected by the emitter

    assert e2["event"] == "step_completed"
    assert e2["status"] == "committed"
    assert e2["bc_correlation_id"] == "corr-1"
    assert e2["duration_ms"] == 287


def test_emitter_with_no_fd_is_noop():
    """When the user didn't pass --progress-fd, the emitter is a no-op
    so command code can call ``emit()`` unconditionally."""
    emitter = ProgressEmitter(fd=None)
    # Must not raise; nothing gets written.
    emitter.emit(event="step_started", seq=1, method="POST", endpoint="x")
    emitter.close()


def test_emitter_close_closes_fd(tmp_path: Path):
    """Closing the emitter releases the fd so the consumer EOFs."""
    out = tmp_path / "events.jsonl"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    emitter = ProgressEmitter(fd=fd)
    emitter.emit(event="x", seq=1)
    emitter.close()
    # Second close must be safe.
    emitter.close()
    # Writing to the fd post-close raises.
    with pytest.raises(OSError):
        os.write(fd, b"after close\n")


def test_emitter_ts_is_iso_z_format(tmp_path: Path):
    out = tmp_path / "evt.jsonl"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    emitter = ProgressEmitter(fd=fd)
    emitter.emit(event="x", seq=1)
    emitter.close()
    e = json.loads(out.read_text().splitlines()[0])
    # ISO 8601 with Z suffix matches the rest of bcli's audit/telemetry.
    assert e["ts"].endswith("Z")


# ─── Typer-default detection ─────────────────────────────────────────


def test_is_real_value_detects_typer_defaults():
    """OptionInfo / ParameterInfo instances from Typer must be treated as
    "not provided" so tests that call the command function directly
    without keyword arguments don't accidentally enable the fd path."""
    class _Stub:
        pass
    stub = _Stub()
    stub.__class__.__name__ = "OptionInfo"
    assert _is_real_value(stub) is False
    assert _is_real_value(None) is False
    assert _is_real_value(3) is True
    assert _is_real_value("/dev/stdout") is True


# ─── batch run end-to-end through the emitter ────────────────────────


def test_batch_run_emits_step_events_to_progress_fd(tmp_path: Path, monkeypatch):
    """End-to-end: --progress-fd 7 writes step_started + step_completed
    events for each step in the manifest."""
    import asyncio
    from unittest.mock import AsyncMock

    from bcli.workflow._models import StepResult, WorkflowContext  # noqa: F401
    from bcli_cli._progress import ProgressEmitter
    from bcli_cli.commands import batch_cmd

    # Build a fake async-with client that responds to .post.
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value={"systemId": "rec-1"})
    fake_client._resolve_url = lambda *a, **kw: "https://example.com/api/vendors"

    class _CM:
        async def __aenter__(self_inner):
            return fake_client
        async def __aexit__(self_inner, *a):
            return False

    monkeypatch.setattr(
        "bcli_cli.commands.batch_cmd.state.make_async_client",
        lambda **_: _CM(),
    )

    out = tmp_path / "events.jsonl"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)

    # Directly exercise _execute_batch with a progress emitter.
    progress = ProgressEmitter(fd=fd)
    steps = [
        {"action": "post", "endpoint": "vendors", "data": {"name": "A"}},
        {"action": "post", "endpoint": "vendors", "data": {"name": "B"}},
    ]
    asyncio.run(batch_cmd._execute_batch(steps, progress=progress))
    progress.close()

    events = [json.loads(line) for line in out.read_text().splitlines() if line]
    # Each step → 1 started + 1 completed.
    started = [e for e in events if e["event"] == "step_started"]
    completed = [e for e in events if e["event"] == "step_completed"]
    assert len(started) == 2
    assert len(completed) == 2
    # Step sequence numbers in order.
    assert [e["seq"] for e in started] == [1, 2]
    assert [e["seq"] for e in completed] == [1, 2]
    # Each completed event names the outcome.
    for e in completed:
        assert e["status"] in {"committed", "failed", "error"}
        assert e["method"] == "POST"
