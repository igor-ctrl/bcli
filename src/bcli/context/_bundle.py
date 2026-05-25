"""Bundle assembly (R4) — pure function turning state into a ContextBundle.

:func:`build_bundle` is the central composer. It reads:

* The persisted last-error record (R6 — no traceback by default).
* The rolling ``bcli.http`` tail (R5/R6 — opt-in via config).
* Profile snapshot (name/env/company — never secrets).
* Optional describe excerpt (caller passes a string; this module does
  no subprocessing — callers like ``bcli ask`` decide whether to
  invoke ``bcli describe``).
* User ``--attach`` files (caller-provided; we redact + budget here).

…then truncates to a caller-supplied token budget in priority order:

    question > last_error > profile_snapshot > recent_http > describe > attachments

and emits a frozen :class:`ContextBundle` with every redaction logged.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bcli.context._http_tail import read_http_tail
from bcli.context._last_error import read_last_error
from bcli.context._protocol import (
    Attachment,
    BundlePolicy,
    BundleSource,
    ContextBundle,
    HttpEvent,
    LastErrorRecord,
    ProfileSnapshot,
    RedactionRecord,
    TokenBudget,
)
from bcli.context._redact import (
    redact_text,
    redact_url,
    scan_attachment,
)


# Rough 4 chars per token heuristic (matches the SDK estimator). The
# bundle's truncation is a *soft* cap — we don't try to nail the
# byte; we walk sources in priority order and stop when we'd exceed.
_CHARS_PER_TOKEN = 4


def build_bundle(
    *,
    question: str = "",
    profile: ProfileSnapshot | None = None,
    policy: BundlePolicy | None = None,
    budget: TokenBudget | None = None,
    describe_excerpt: str = "",
    registry_snapshot_hash: str = "",
    attachments: tuple[Attachment, ...] | None = None,
    raw_attachments: tuple[tuple[str, str], ...] = (),
    last_error: LastErrorRecord | None = None,
    skip_last_error: bool = False,
    recent_http: tuple[HttpEvent, ...] | None = None,
    config_dir: Path | None = None,
) -> ContextBundle:
    """Build a model-bound :class:`ContextBundle` from the live state.

    Every named arg has a sensible default so a "minimal" ask
    (``--no-context``) lands a bundle with just the question and
    policy stub.

    ``raw_attachments`` is the operator-friendly shape ``(label,
    content)`` — they're redacted + truncated and added to
    ``attachments``. Pre-built :class:`Attachment` instances passed via
    ``attachments`` skip the scan (assumed already post-redaction).

    ``skip_last_error=True`` disables the implicit "read
    ``last-error.json`` from disk when no record was passed" — used by
    ``bcli ask --no-context`` to genuinely strip recent failures from
    the bundle. The caller passing ``last_error=somerecord`` is
    unaffected.
    """
    policy = policy or BundlePolicy()
    budget = budget or TokenBudget()
    profile = profile or ProfileSnapshot()
    redactions: list[RedactionRecord] = []
    sources: list[BundleSource] = []
    actual_chars = 0
    truncated = False

    # Always include the question — non-negotiable.
    if question:
        actual_chars += len(question)
        sources.append(BundleSource(
            kind="question",
            label="user-question",
            included_bytes=len(question.encode("utf-8")),
        ))

    # Last error — second priority. Caller-provided OR read from disk
    # unless skip_last_error is set (the --no-context contract).
    le = last_error
    if le is None and not skip_last_error:
        le = read_last_error(config_dir=config_dir)
    if le is not None:
        # Redact bc_message + url if not already done (the persisted
        # file is already redacted, but a caller-provided record may
        # not be — defensive).
        clean_le, le_recs = _redact_last_error(le)
        redactions.extend(le_recs)
        le_bytes = _estimate_bytes(clean_le)
        actual_chars += le_bytes
        sources.append(BundleSource(
            kind="last_error",
            label="last-error.json",
            included_bytes=le_bytes,
        ))
        le = clean_le

    # Profile snapshot — third priority. Cheap and always safe.
    if any((profile.name, profile.environment, profile.company)):
        ps_bytes = _estimate_bytes(profile)
        actual_chars += ps_bytes
        sources.append(BundleSource(
            kind="profile_snapshot",
            label="profile",
            included_bytes=ps_bytes,
        ))

    # Recent HTTP — fourth priority. Read from disk unless provided.
    http_events: tuple[HttpEvent, ...] = ()
    if policy.include_http_tail:
        http_events = recent_http or read_http_tail(config_dir=config_dir)
        if http_events:
            # Tail is already URL-redacted on read, but the bundle is
            # the source-of-truth audit so we re-walk to catch any
            # caller-provided events.
            cleaned, recs = _redact_http_events(http_events)
            redactions.extend(recs)
            http_bytes = sum(_estimate_bytes(h) for h in cleaned)
            # Apply budget truncation here — drop oldest if over.
            while cleaned and actual_chars + http_bytes > budget.max_tokens * _CHARS_PER_TOKEN:
                dropped = cleaned[0]
                cleaned = cleaned[1:]
                http_bytes -= _estimate_bytes(dropped)
                truncated = True
            actual_chars += http_bytes
            http_events = cleaned
            if http_events:
                sources.append(BundleSource(
                    kind="http_tail",
                    label=f"recent-http ({len(http_events)} events)",
                    included_bytes=http_bytes,
                ))

    # Describe excerpt — fifth priority. Drop if it'd blow the budget.
    if policy.include_describe and describe_excerpt:
        desc_bytes = len(describe_excerpt)
        if actual_chars + desc_bytes <= budget.max_tokens * _CHARS_PER_TOKEN:
            actual_chars += desc_bytes
            sources.append(BundleSource(
                kind="describe",
                label="describe-excerpt",
                included_bytes=desc_bytes,
            ))
        else:
            describe_excerpt = ""
            truncated = True

    # Attachments — lowest priority. Scan each then budget.
    out_attachments: list[Attachment] = list(attachments or ())
    for label, content in raw_attachments:
        cleaned, included_bytes, recs = scan_attachment(
            content,
            label=label,
            max_bytes=policy.attachment_max_bytes,
            redact_guids=policy.redact_company_ids,
        )
        redactions.extend(recs)
        if actual_chars + included_bytes > budget.max_tokens * _CHARS_PER_TOKEN:
            truncated = True
            continue
        actual_chars += included_bytes
        out_attachments.append(Attachment(
            label=label,
            path="",
            content=cleaned,
            original_bytes=len(content.encode("utf-8")),
            included_bytes=included_bytes,
        ))
        sources.append(BundleSource(
            kind="attachment",
            label=label,
            included_bytes=included_bytes,
        ))

    actual_tokens = (actual_chars + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN
    if actual_tokens > budget.max_tokens:
        truncated = True

    final_budget = TokenBudget(
        max_tokens=budget.max_tokens,
        actual_tokens=actual_tokens,
        truncated=truncated,
    )

    # Hash the registry only when caller didn't supply one. We don't
    # want to import the registry here (would create a heavy dep);
    # leave the hash to the caller in the common case.
    snapshot_hash = registry_snapshot_hash

    return ContextBundle(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        question=question,
        budget=final_budget,
        policy=policy,
        sources=tuple(sources),
        redactions=tuple(redactions),
        profile_snapshot=profile,
        registry_snapshot_hash=snapshot_hash,
        describe_excerpt=describe_excerpt,
        recent_http=http_events,
        last_error=le,
        attachments=tuple(out_attachments),
    )


def _redact_last_error(
    le: LastErrorRecord,
) -> tuple[LastErrorRecord, tuple[RedactionRecord, ...]]:
    records: list[RedactionRecord] = []
    new_url, recs = redact_url(le.url, location_path="last_error.url")
    records.extend(recs)
    new_msg, recs = redact_text(
        le.bc_message, location_path="last_error.bc_message"
    )
    records.extend(recs)
    new_hint, recs = redact_text(le.hint, location_path="last_error.hint")
    records.extend(recs)
    cleaned = LastErrorRecord(
        timestamp=le.timestamp,
        command=le.command,
        error_class=le.error_class,
        exit_code=le.exit_code,
        status=le.status,
        profile=le.profile,
        environment=le.environment,
        company=le.company,
        url=new_url,
        method=le.method,
        correlation_id=le.correlation_id,
        endpoint=le.endpoint,
        hint=new_hint,
        bc_message=new_msg,
        traceback_excerpt=le.traceback_excerpt,
    )
    return cleaned, tuple(records)


def _redact_http_events(
    events: tuple[HttpEvent, ...],
) -> tuple[tuple[HttpEvent, ...], tuple[RedactionRecord, ...]]:
    records: list[RedactionRecord] = []
    cleaned: list[HttpEvent] = []
    for i, ev in enumerate(events):
        new_url, recs = redact_url(
            ev.url, location_path=f"recent_http[{i}].url"
        )
        records.extend(recs)
        cleaned.append(HttpEvent(
            timestamp=ev.timestamp,
            method=ev.method,
            url=new_url,
            status=ev.status,
            latency_ms=ev.latency_ms,
            correlation_id=ev.correlation_id,
            endpoint=ev.endpoint,
            retry_count=ev.retry_count,
        ))
    return tuple(cleaned), tuple(records)


def _estimate_bytes(value: Any) -> int:
    """Rough byte-estimate of a dataclass / scalar for budget math."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return len(str(value))
    if isinstance(value, str):
        return len(value)
    if hasattr(value, "__dict__"):
        # Dataclass-ish — sum string fields.
        total = 0
        for v in vars(value).values():
            total += _estimate_bytes(v)
        return total
    # For frozen dataclasses use __dataclass_fields__ if present.
    if hasattr(value, "__dataclass_fields__"):
        total = 0
        for f in value.__dataclass_fields__:
            total += _estimate_bytes(getattr(value, f))
        return total
    try:
        return len(str(value))
    except Exception:  # noqa: BLE001
        return 0


# Helper exposed for the hash-the-registry caller (ask, agent).
def hash_registry(payload: bytes | str) -> str:
    """Produce a stable sha256 hex of a registry serialisation."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return "sha256:" + hashlib.sha256(data).hexdigest()


__all__ = ["build_bundle", "hash_registry"]
