"""CLI-side result-envelope wrapper for mutating commands.

Sibling to :mod:`bcli_cli._audit_wrap`. Where audit emits one entry to a
JSONL sink (for compliance/forensics), the envelope writes a single JSON
object to a side channel an agent runtime parses (``--result-out PATH``
or ``--result-fd N``).

The two concerns intentionally stay separate:

* Audit logs are an admin opt-in, profile-scoped, multi-call append-only.
* Envelopes are per-invocation, requested explicitly per command call,
  and consumed by a programmatic caller (MCP, CI, agent runtime).

Five commands need the same wiring (``post``, ``patch``, ``delete``,
``attach upload``, ``batch run``) so this module owns the lifecycle:

1. :func:`validate_flags` — raise ``BadParameter`` if both flags are set.
2. :func:`Capture` context manager — start_at + uuid.
3. ``Capture.emit_*`` helpers — build the envelope and write it.

The wrapper never raises if the user didn't pass ``--result-out`` /
``--result-fd``: that keeps the flag strictly opt-in (additive only).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import typer

from bcli._version import __version__
from bcli.exit_codes import EXIT_GENERIC_ERROR, EXIT_OK, exit_code_for_status
from bcli.result_envelope import (
    ENVELOPE_VERSION,
    ResultEnvelope,
    write_envelope,
)
from bcli_cli._state import state


def _is_real_value(v: object) -> bool:
    """True if ``v`` is a value the user actually provided.

    ``typer.Option`` instances leak through when a command function is
    called directly (not via Typer's CLI parsing) without explicit
    keyword arguments — that's the pattern existing tests use to invoke
    ``post_command`` / ``run_batch`` etc. Treat those as "not provided"
    so a test that doesn't care about the envelope flags doesn't trip
    the mutual-exclusion guard.
    """
    if v is None:
        return False
    # Be tolerant: Typer wraps defaults in click's OptionInfo / ParameterInfo.
    cls_name = type(v).__name__
    if cls_name in {"OptionInfo", "ArgumentInfo", "ParameterInfo"}:
        return False
    return True


def validate_flags(
    result_out: Path | None,
    result_fd: int | None,
) -> None:
    """Raise ``typer.BadParameter`` if the user passed both flags.

    Mutual exclusion is checked here (not in Typer's ``Option``) so the
    error path is identical across the five commands and the spec test
    can pin a single failure mode.
    """
    if _is_real_value(result_out) and _is_real_value(result_fd):
        raise typer.BadParameter(
            "--result-out and --result-fd are mutually exclusive; pass one or the other.",
        )


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_id_from(body: Any) -> str | None:
    """Pull a record id out of a response body for the envelope.

    Order:
    1. ``systemId`` — BC's canonical immutable id, our preferred shape.
    2. ``id`` — fallback for endpoints that don't surface ``systemId``.
    3. First key that ends with ``Id`` (case-insensitive).
    """
    if not isinstance(body, dict):
        return None
    for key in ("systemId", "id"):
        if key in body and body[key] is not None:
            return str(body[key])
    for key, value in body.items():
        if value is None:
            continue
        if key.lower().endswith("id"):
            return str(value)
    return None


@dataclass
class _CaptureState:
    """Mutable bag used by the context manager."""

    started_at: str
    started_monotonic_ns: int
    invocation_id: str
    method: str
    endpoint: str
    result_out: Path | None
    result_fd: int | None
    resolved_url: str | None = None
    record_id: str | None = None
    dry_run: bool = False
    written: bool = False  # guard against double-emit


def _build_envelope(
    cap: _CaptureState,
    *,
    status: str,
    exit_code: int,
    bc_correlation_id: str | None,
) -> ResultEnvelope:
    import time as _time

    duration_ns = _time.monotonic_ns() - cap.started_monotonic_ns
    duration_ms = max(0, int(duration_ns / 1_000_000))

    profile = None
    environment = None
    company = None
    profile_name = state.active_profile_name
    try:
        prof = state.profile
        environment = prof.environment
        company = prof.company_id
        profile = profile_name
    except Exception:
        # Defensive: building the profile shouldn't kill the envelope write.
        profile = profile_name

    return ResultEnvelope(
        version=ENVELOPE_VERSION,
        invocation_id=cap.invocation_id,
        tool_version=__version__,
        profile=profile,
        environment=environment,
        company=company,
        method=cap.method.upper(),
        endpoint=cap.endpoint,
        resolved_url=cap.resolved_url,
        record_id=cap.record_id,
        dry_run=cap.dry_run,
        status=status,
        exit_code=exit_code,
        bc_correlation_id=bc_correlation_id,
        telemetry_event_id=None,
        audit_log_offset=None,
        started_at=cap.started_at,
        duration_ms=duration_ms,
    )


def _write_if_active(cap: _CaptureState, env: ResultEnvelope) -> None:
    if cap.result_out is None and cap.result_fd is None:
        return
    if cap.written:
        return
    write_envelope(env, path=cap.result_out, fd=cap.result_fd)
    cap.written = True


class Capture:
    """Active capture handle returned by :func:`capture`.

    The command wires these calls:

    * :meth:`set_resolved_url`  — once the URL is known (pre-flight).
    * :meth:`set_record_id`     — after a successful response.
    * :meth:`mark_dry_run`      — when the command short-circuits.
    * :meth:`emit_success`      — happy path.
    * :meth:`emit_failure`      — exception path.

    The :func:`capture` context manager emits an envelope on its way out
    if the command didn't already, so a forgotten ``emit_*`` call still
    produces output (failure path).
    """

    def __init__(self, cap: _CaptureState) -> None:
        self._cap = cap

    @property
    def is_active(self) -> bool:
        return self._cap.result_out is not None or self._cap.result_fd is not None

    def set_resolved_url(self, url: str | None) -> None:
        self._cap.resolved_url = url

    def set_record_id(self, value: str | None) -> None:
        self._cap.record_id = value

    def extract_record_id_from(self, response: Any) -> None:
        """Pull the record id out of a response and stash it."""
        rid = _record_id_from(response)
        if rid is not None:
            self._cap.record_id = rid

    def mark_dry_run(self) -> None:
        self._cap.dry_run = True

    def emit_success(self, *, bc_correlation_id: str | None = None) -> None:
        if not self.is_active:
            return
        env = _build_envelope(
            self._cap,
            status="succeeded",
            exit_code=EXIT_OK,
            bc_correlation_id=bc_correlation_id,
        )
        _write_if_active(self._cap, env)

    def emit_failure(self, exc: BaseException) -> None:
        if not self.is_active:
            return
        status_code = getattr(exc, "status_code", None)
        correlation_id = getattr(exc, "correlation_id", None)
        exit_code = exit_code_for_status(status_code)
        if exit_code == EXIT_GENERIC_ERROR and status_code is None:
            exit_code = EXIT_GENERIC_ERROR
        env = _build_envelope(
            self._cap,
            status="failed",
            exit_code=exit_code,
            bc_correlation_id=correlation_id,
        )
        _write_if_active(self._cap, env)


@contextmanager
def capture(
    *,
    method: str,
    endpoint: str,
    result_out: Path | None,
    result_fd: int | None,
) -> Iterator[Capture]:
    """Context manager that captures invocation metadata and writes the envelope.

    Usage:

        with capture(method="POST", endpoint=endpoint,
                     result_out=result_out, result_fd=result_fd) as cap:
            cap.set_resolved_url(try_resolve_url(endpoint, ...))
            try:
                result = await ...
                cap.extract_record_id_from(result)
                cap.emit_success()
            except Exception as exc:
                cap.emit_failure(exc)
                raise

    When the inner block raises without calling ``emit_failure`` (or
    ``typer.Exit`` slipping past), the context manager emits a generic
    failure envelope on the way out so the side channel always has a
    record when the flag was passed.
    """
    import time as _time

    real_out = result_out if _is_real_value(result_out) else None
    real_fd = result_fd if _is_real_value(result_fd) else None
    cap_state = _CaptureState(
        started_at=_now_iso_utc(),
        started_monotonic_ns=_time.monotonic_ns(),
        invocation_id=uuid.uuid4().hex,
        method=method,
        endpoint=endpoint,
        result_out=Path(real_out) if real_out is not None else None,
        result_fd=real_fd,
    )
    handle = Capture(cap_state)
    try:
        yield handle
    except BaseException as exc:  # noqa: BLE001
        # If the command body raised without telling us, salvage an envelope.
        # typer.Exit with exit_code=0 still means the command short-circuited
        # (dry-run flow) — leave it alone if dry-run was marked + success emitted.
        if isinstance(exc, typer.Exit):
            # On clean Exit (code 0/None) treat as success if not already
            # written; non-zero Exit means a failure path the caller flagged.
            code = exc.exit_code or 0
            if not cap_state.written:
                if code == 0:
                    env = _build_envelope(
                        cap_state,
                        status="succeeded",
                        exit_code=EXIT_OK,
                        bc_correlation_id=None,
                    )
                else:
                    env = _build_envelope(
                        cap_state,
                        status="failed",
                        exit_code=int(code) if isinstance(code, int) else EXIT_GENERIC_ERROR,
                        bc_correlation_id=None,
                    )
                _write_if_active(cap_state, env)
            raise
        # Non-Exit exception we didn't see — record it and re-raise.
        if not cap_state.written:
            handle.emit_failure(exc)
        raise
    else:
        # Clean exit path — caller already invoked emit_success on the happy
        # path. If they didn't, write a default success envelope.
        if not cap_state.written and (
            cap_state.result_out is not None or cap_state.result_fd is not None
        ):
            env = _build_envelope(
                cap_state,
                status="succeeded",
                exit_code=EXIT_OK,
                bc_correlation_id=None,
            )
            _write_if_active(cap_state, env)


__all__ = ["Capture", "capture", "validate_flags"]


# Re-export for unit tests / direct use:
def record_id_from(body: Any) -> str | None:  # pragma: no cover - thin shim
    return _record_id_from(body)
