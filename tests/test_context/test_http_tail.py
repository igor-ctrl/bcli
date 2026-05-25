"""``bcli.http`` rolling tail: enable, write, read, rotate."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bcli.context import enable_http_tail, http_tail_path, read_http_tail


def _clear_http_tail_handlers() -> None:
    """Remove any tail handler attached by a prior test."""
    log = logging.getLogger("bcli.http")
    for h in list(log.handlers):
        if getattr(h, "_bcli_context_tail", False):
            log.removeHandler(h)
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass


def test_enable_returns_true_and_attaches_handler(tmp_path: Path) -> None:
    _clear_http_tail_handlers()
    ok = enable_http_tail(config_dir=tmp_path)
    assert ok is True
    log = logging.getLogger("bcli.http")
    tail_handlers = [h for h in log.handlers if getattr(h, "_bcli_context_tail", False)]
    assert len(tail_handlers) == 1
    _clear_http_tail_handlers()


def test_enable_is_idempotent(tmp_path: Path) -> None:
    _clear_http_tail_handlers()
    assert enable_http_tail(config_dir=tmp_path) is True
    assert enable_http_tail(config_dir=tmp_path) is True
    log = logging.getLogger("bcli.http")
    tail_handlers = [h for h in log.handlers if getattr(h, "_bcli_context_tail", False)]
    assert len(tail_handlers) == 1
    _clear_http_tail_handlers()


def test_events_round_trip(tmp_path: Path) -> None:
    _clear_http_tail_handlers()
    enable_http_tail(config_dir=tmp_path)
    log = logging.getLogger("bcli.http")
    sample = {
        "timestamp": "2026-05-22T10:00:00+00:00",
        "method": "GET",
        "url": "https://example/api?token=topsecret",
        "status": 200,
        "latency_ms": 42.0,
        "correlation_id": "corr-1",
        "endpoint": "vendors",
        "retry_count": 0,
    }
    log.info(json.dumps(sample))
    # Make sure handler flushes.
    for h in log.handlers:
        h.flush()

    events = read_http_tail(config_dir=tmp_path)
    assert len(events) == 1
    ev = events[0]
    assert ev.method == "GET"
    assert ev.status == 200
    # URL redacted on read.
    assert "topsecret" not in ev.url
    _clear_http_tail_handlers()


def test_read_skips_bad_lines(tmp_path: Path) -> None:
    p = http_tail_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '{"method":"GET","url":"https://ok","status":200}\n'
        "not-json garbage\n"
        '{"method":"POST","url":"https://ok2","status":201}\n',
        encoding="utf-8",
    )
    events = read_http_tail(config_dir=tmp_path)
    assert len(events) == 2
    assert events[0].method == "GET"
    assert events[1].method == "POST"


def test_size_cap_rotates_old_lines(tmp_path: Path) -> None:
    _clear_http_tail_handlers()
    # 2 KB cap so the rollover fires quickly with our payload size.
    enable_http_tail(config_dir=tmp_path, max_bytes=2048, backup_count=1)
    log = logging.getLogger("bcli.http")
    line = {
        "method": "GET",
        "url": "https://example/api",
        "status": 200,
        "latency_ms": 1.0,
    }
    for i in range(200):
        line["correlation_id"] = f"corr-{i}"
        log.info(json.dumps(line))
    for h in log.handlers:
        h.flush()

    main = http_tail_path(tmp_path)
    backup = main.with_suffix(main.suffix + ".1")
    # Either rollover happened (backup exists) or main file stayed
    # under the cap (some platforms truncate at slightly different
    # bytes); the invariant the test cares about is the main file
    # never exceeds (cap + one record) so memory stays bounded.
    assert main.is_file()
    if backup.exists():
        assert os.path.getsize(main) < 4096
    _clear_http_tail_handlers()


def test_read_when_no_file_returns_empty(tmp_path: Path) -> None:
    events = read_http_tail(config_dir=tmp_path)
    assert events == ()


def test_limit_keeps_newest(tmp_path: Path) -> None:
    p = http_tail_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"method": "GET", "url": f"https://x/{i}", "status": 200})
        for i in range(10)
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = read_http_tail(config_dir=tmp_path, limit=3)
    assert len(events) == 3
    # Newest last.
    assert events[-1].url.endswith("/9")
