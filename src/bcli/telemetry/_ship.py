"""Ship spooled telemetry to Application Insights. Runs detached.

Started by :meth:`AzureMonitorSink.flush` as
``python -m bcli.telemetry._ship <spool_dir> <track_url>`` after the command
has returned, so the network round trip never delays the user.

* One shipper at a time, via an ``O_EXCL`` lock file. A lock older than
  :data:`LOCK_STALE_SECONDS` belongs to a shipper that died and is taken over.
* The live spool is claimed by renaming it to an ``outbox-*.jsonl`` file, so
  commands that finish meanwhile start a fresh spool. The shipper loops until
  no spool or outbox is left.
* An outbox is deleted once Application Insights has answered. On a network
  error or a retryable status the unsent remainder stays on disk for the
  next run.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

from bcli.telemetry._azure_monitor import SPOOL_NAME

LOCK_NAME = "ship.lock"
LOCK_STALE_SECONDS = 300
BATCH_SIZE = 500
TIMEOUT_SECONDS = 10.0
# Application Insights' documented retryable statuses. Anything else (200,
# 206 partial accept, 400 malformed) is final: retrying can't change it.
RETRYABLE = frozenset({408, 429, 439, 500, 502, 503, 504})


def _acquire(lock: Path) -> bool:
    for _ in range(2):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue  # released between our open and stat; try again
            if age < LOCK_STALE_SECONDS:
                return False
            lock.unlink(missing_ok=True)
    return False


def _valid_lines(path: Path) -> list[str]:
    # A malformed line would make the whole batch a 400, losing the good
    # events with it, so drop unparseable lines up front.
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            json.loads(line)
        except ValueError:
            continue
        lines.append(line)
    return lines


def _send(client: httpx.Client, url: str, lines: list[str]) -> bool:
    """True when Application Insights has given a final answer for these lines."""
    try:
        resp = client.post(
            url,
            content="[" + ",".join(lines) + "]",
            headers={"Content-Type": "application/json"},
        )
    except httpx.HTTPError:
        return False
    return resp.status_code not in RETRYABLE


def ship(spool_dir: Path, url: str, client: httpx.Client | None = None) -> None:
    lock = spool_dir / LOCK_NAME
    if not _acquire(lock):
        return
    try:
        with client or httpx.Client(timeout=TIMEOUT_SECONDS) as http:
            while True:
                spool = spool_dir / SPOOL_NAME
                if spool.exists():
                    os.replace(spool, spool_dir / f"outbox-{time.time_ns()}.jsonl")
                    # A writer that opened the spool just before the rename
                    # appends to the outbox instead; give that write a moment.
                    time.sleep(0.2)
                outboxes = sorted(spool_dir.glob("outbox-*.jsonl"))
                if not outboxes:
                    return
                for outbox in outboxes:
                    lines = _valid_lines(outbox)
                    for start in range(0, len(lines), BATCH_SIZE):
                        os.utime(lock)  # still alive: keep the lock fresh
                        if not _send(http, url, lines[start:start + BATCH_SIZE]):
                            outbox.write_text("\n".join(lines[start:]) + "\n", encoding="utf-8")
                            return
                    outbox.unlink(missing_ok=True)
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    try:
        ship(Path(argv[0]), argv[1])
    except Exception:  # noqa: BLE001 — detached, nobody to report to
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
