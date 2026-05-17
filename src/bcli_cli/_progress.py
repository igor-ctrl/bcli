"""AIP §Phase 4e — JSON progress events on a dedicated file descriptor.

For long-running ``bcli batch run`` / ``bcli extract run`` work, agents
want per-step structured events without having to scrape stderr. We
write one JSON object per line to ``--progress-fd N``:

::

    {"event": "step_started",   "seq": 3, "method": "POST", ...}
    {"event": "step_completed", "seq": 3, "status": "committed", ...}

Stderr stays human-readable (progress bars, Rich tables); the fd channel
is structured and stable. Using a separate fd from ``--result-fd``
(Phase 2) lets a caller demux: result envelope is one final object,
progress events are a stream.

The emitter is a no-op when ``fd is None`` so command code can call
``emit()`` unconditionally without a guard.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def _is_real_value(v: object) -> bool:
    """Mirror of ``_envelope_wrap._is_real_value`` — recognise Typer defaults.

    Tests call command functions directly without keyword arguments, so
    Typer's ``OptionInfo`` instances leak through as the default. Treat
    them as "not provided" so the no-fd path is exercised correctly.
    """
    if v is None:
        return False
    cls_name = type(v).__name__
    if cls_name in {"OptionInfo", "ArgumentInfo", "ParameterInfo"}:
        return False
    return True


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ProgressEmitter:
    """Write JSON-lines to a file descriptor.

    Construction with ``fd=None`` produces a no-op instance; callers can
    invoke :meth:`emit` unconditionally.
    """

    def __init__(self, fd: int | None) -> None:
        self._fd = fd if _is_real_value(fd) else None
        self._closed = False

    @property
    def is_active(self) -> bool:
        return self._fd is not None and not self._closed

    def emit(self, **fields: Any) -> None:
        """Write one JSON object to the fd. Injects an ISO-8601 ``ts``.

        Silently drops the event if no fd is set; that's by design so
        command code stays simple.
        """
        if not self.is_active:
            return
        event = {"ts": _now_iso_utc(), **fields}
        payload = json.dumps(event, default=str) + "\n"
        try:
            os.write(self._fd, payload.encode("utf-8"))  # type: ignore[arg-type]
        except OSError:
            # The consumer closed its end of the pipe — disable further
            # writes rather than crash the whole batch.
            self._closed = True

    def close(self) -> None:
        """Close the fd if we still own it. Idempotent."""
        if self._closed or self._fd is None:
            self._closed = True
            return
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._closed = True


__all__ = ["ProgressEmitter", "_is_real_value"]
