"""Tests for the detached telemetry shipper (bcli.telemetry._ship)."""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

from bcli.telemetry import _ship

URL = "https://example.in.applicationinsights.azure.com/v2.1/track"


def _event(n: int) -> str:
    return json.dumps({"name": "Microsoft.ApplicationInsights.Event", "n": n})


def _client(statuses, received):
    """httpx client answering with `statuses` in order; `received` collects accepted batches."""
    it = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(it)
        if isinstance(status, Exception):
            raise status
        if 200 <= status < 300:
            received.append([item["n"] for item in json.loads(request.content)])
        return httpx.Response(status)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(_ship.time, "sleep", lambda s: None)


def _write_spool(tmp_path, ns):
    (tmp_path / "spool.jsonl").write_text("".join(_event(n) + "\n" for n in ns))


def _leftovers(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir())


def test_ships_spool_and_cleans_up(tmp_path):
    _write_spool(tmp_path, [1, 2, 3])
    received = []
    _ship.ship(tmp_path, URL, _client([200], received))
    assert received == [[1, 2, 3]]
    assert _leftovers(tmp_path) == []  # spool, outbox and lock all gone


def test_batches_large_spools(tmp_path, monkeypatch):
    monkeypatch.setattr(_ship, "BATCH_SIZE", 2)
    _write_spool(tmp_path, [1, 2, 3, 4, 5])
    received = []
    _ship.ship(tmp_path, URL, _client([200, 200, 200], received))
    assert received == [[1, 2], [3, 4], [5]]


@pytest.mark.parametrize("failure", [503, 429, httpx.ConnectError("offline")])
def test_retryable_failure_keeps_unsent_events(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(_ship, "BATCH_SIZE", 2)
    _write_spool(tmp_path, [1, 2, 3, 4])
    received = []
    _ship.ship(tmp_path, URL, _client([200, failure], received))
    assert received == [[1, 2]]
    (outbox,) = tmp_path.glob("outbox-*.jsonl")
    assert [json.loads(line)["n"] for line in outbox.read_text().splitlines()] == [3, 4]
    assert not (tmp_path / "ship.lock").exists()

    # The next run picks the leftover up.
    received = []
    _ship.ship(tmp_path, URL, _client([200], received))
    assert received == [[3, 4]]
    assert _leftovers(tmp_path) == []


def test_final_rejection_is_not_retried(tmp_path):
    _write_spool(tmp_path, [1])
    received = []
    _ship.ship(tmp_path, URL, _client([400], received))
    assert received == []
    assert _leftovers(tmp_path) == []  # dropped, not kept for a retry that would fail again


def test_malformed_lines_are_dropped_not_sent(tmp_path):
    (tmp_path / "spool.jsonl").write_text(_event(1) + "\n{truncated\n" + _event(2) + "\n")
    received = []
    _ship.ship(tmp_path, URL, _client([200], received))
    assert received == [[1, 2]]


def test_fresh_lock_means_another_shipper_is_running(tmp_path):
    _write_spool(tmp_path, [1])
    (tmp_path / "ship.lock").touch()
    received = []
    _ship.ship(tmp_path, URL, _client([], received))
    assert received == []
    assert (tmp_path / "spool.jsonl").exists()


def test_stale_lock_is_taken_over(tmp_path):
    _write_spool(tmp_path, [1])
    lock = tmp_path / "ship.lock"
    lock.touch()
    old = time.time() - _ship.LOCK_STALE_SECONDS - 1
    os.utime(lock, (old, old))
    received = []
    _ship.ship(tmp_path, URL, _client([200], received))
    assert received == [[1]]
    assert _leftovers(tmp_path) == []


def test_main_never_raises(tmp_path):
    assert _ship.main([str(tmp_path / "missing"), URL]) == 0
