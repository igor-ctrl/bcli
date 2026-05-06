"""CLI-side audit-log integration.

Two entry points used by the write commands:

* :func:`audited_write` wraps the actual write coroutine, emitting one
  ``completed`` or ``failed`` audit entry per call.
* :func:`emit_dry_run_audit` is called by ``render_dry_run`` to record
  the ``dry_run`` outcome before short-circuiting.

Both fast-path when audit is disabled (no sink construction, no work
beyond a property read) so the audit feature has zero overhead until
the user opts in.

The SDK (``AsyncBCClient``) intentionally does NOT auto-emit — programmatic
users get unfiltered access. Audit is a CLI-layer ergonomic, layered on
top of BC's own permission set (the actual security boundary).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, TypeVar

from bcli._version import __version__
from bcli.audit import AuditEntry, get_audit_sink, redact
from bcli_cli._state import state

T = TypeVar("T")

_CALLER = "cli"


def _profile_audit_sink():
    return get_audit_sink(state.config.audit, profile_name=state.active_profile_name)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redact_body(body: Any | None) -> Any | None:
    if body is None:
        return None
    return redact(body, state.config.audit.redact_keys)


async def audited_write(
    coro: Awaitable[T],
    *,
    method: str,
    endpoint: str,
    body: Any | None = None,
    record_id: str | None = None,
    resolved_url: str | None = None,
) -> T:
    """Run a write coroutine, emitting an audit entry on completion or failure.

    Re-raises any exception from the wrapped coroutine after recording it,
    so callers see the same surface they would without auditing.
    """
    sink = _profile_audit_sink()
    if not sink.is_active:
        return await coro

    profile = state.profile
    redacted = _redact_body(body)
    start = time.perf_counter()

    try:
        result = await coro
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        sink.emit(
            AuditEntry(
                ts=_now_iso(),
                profile=state.active_profile_name,
                environment=profile.environment,
                company_id=profile.company_id,
                method=method.upper(),
                endpoint=endpoint,
                resolved_url=resolved_url,
                record_id=record_id,
                request_body=redacted,
                status=getattr(exc, "status_code", None),
                correlation_id=getattr(exc, "correlation_id", None),
                latency_ms=latency_ms,
                cli_version=__version__,
                caller=_CALLER,
                outcome="failed",
                error=str(exc),
            )
        )
        raise

    latency_ms = int((time.perf_counter() - start) * 1000)
    sink.emit(
        AuditEntry(
            ts=_now_iso(),
            profile=state.active_profile_name,
            environment=profile.environment,
            company_id=profile.company_id,
            method=method.upper(),
            endpoint=endpoint,
            resolved_url=resolved_url,
            record_id=record_id,
            request_body=redacted,
            status=200,  # transport returns body on success; status not surfaced
            correlation_id=None,
            latency_ms=latency_ms,
            cli_version=__version__,
            caller=_CALLER,
            outcome="completed",
            error=None,
        )
    )
    return result


def emit_dry_run_audit(
    method: str,
    endpoint: str,
    *,
    body: Any | None = None,
    record_id: str | None = None,
    resolved_url: str | None = None,
) -> None:
    """Record a ``dry_run`` audit entry. Called from ``render_dry_run``."""
    sink = _profile_audit_sink()
    if not sink.is_active:
        return

    profile = state.profile
    sink.emit(
        AuditEntry(
            ts=_now_iso(),
            profile=state.active_profile_name,
            environment=profile.environment,
            company_id=profile.company_id,
            method=method.upper(),
            endpoint=endpoint,
            resolved_url=resolved_url,
            record_id=record_id,
            request_body=_redact_body(body),
            status=None,
            correlation_id=None,
            latency_ms=None,
            cli_version=__version__,
            caller=_CALLER,
            outcome="dry_run",
            error=None,
        )
    )
