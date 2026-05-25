"""Typed bundle dataclasses for the bcli context layer (Part 0 / R4).

These shapes are the shared model surface that downstream LLM-driven
features (``bcli ask``, future ``bcli agent``, ``bcli explain``, …)
consume. They're *not* free dicts: every field is typed, frozen, and
size-bounded so a redaction regression is catchable in CI and a stale
bundle can be re-hydrated from disk byte-for-byte.

Design notes
------------

* Every dataclass is ``@dataclass(frozen=True)``. The bundle round-trips
  through JSON for cache / replay; mutation would break the
  ``schema_version`` contract and confuse downstream agents.
* Collections inside a frozen dataclass are tuples (``redactions``,
  ``sources``, ``recent_http``, ``attachments``) — list defaults are
  unhashable and break ``frozen=True``.
* ``RedactionRecord`` is the audit trail per R5 — every removed string
  is logged with the rule that matched it. Tests assert that no secret
  leaves the laptop without a corresponding record.
* ``TokenBudget`` is set by the caller. The bundle truncates lowest-
  priority sources first (HTTP tail → describe → attachments) and
  flips ``truncated=True`` so the consumer knows the bundle is a slice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class TokenBudget:
    """Caller-supplied size envelope for the bundle.

    ``max_tokens`` is a soft cap — the bundle generator never trims to
    the exact byte; it walks sources in priority order and stops adding
    when the running total exceeds the cap. ``actual_tokens`` is
    populated post-generation with the count the bundler arrived at,
    using the rough 4-chars-per-token heuristic also used by the
    Anthropic Python SDK's token estimator.
    """

    max_tokens: int = 16_000
    actual_tokens: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class BundleSource:
    """One contributor to the assembled bundle.

    ``kind`` is one of ``"last_error"``, ``"http_tail"``, ``"describe"``,
    ``"attachment"``, ``"profile_snapshot"``, ``"question"`` — the
    consumer uses it to route prompt sections.

    ``included_bytes`` records the post-redaction byte size that actually
    made it into the bundle; if redactions or budget truncated the source
    this will be smaller than the on-disk size.
    """

    kind: str
    label: str
    path: str = ""
    included_bytes: int = 0


@dataclass(frozen=True)
class RedactionRecord:
    """One audit-trail entry for a redacted value.

    Every removed string lands here with the rule that matched it
    (``rule_id``) and the *location* it was found (``location_path`` —
    e.g. ``"recent_http[3].url"``). The original is NEVER stored — only
    its length, so a "wait, that was 4 chars, can't be a token" sanity
    check is possible at audit time. False positives in this trail are
    cheap; false negatives leak credentials.
    """

    rule_id: str
    location_path: str
    redacted_length: int


@dataclass(frozen=True)
class BundlePolicy:
    """Caller-controlled flags driving what the bundler is allowed to read.

    ``include_bodies`` defaults False — request / response bodies stay
    out of the bundle unless the operator opts in (``bcli ask
    --include-bodies``). ``include_debug`` defaults False — the debug
    last-error file (which may carry traceback) is excluded from the
    bundle even when present on disk.

    Mirrors the ``[context]`` section in config: ``redact_company_ids``,
    ``attachment_max_bytes``.
    """

    include_bodies: bool = False
    include_describe: bool = True
    include_http_tail: bool = True
    include_debug: bool = False
    redact_company_ids: bool = False
    attachment_max_bytes: int = 256 * 1024


@dataclass(frozen=True)
class ProfileSnapshot:
    """Minimal profile metadata safe to ship to an LLM.

    Excludes ``tenant_id``, ``client_id``, ``client_secret_env`` —
    those are credential surface, not context. The model only needs to
    know "we were on profile X, environment Y, company Z" to reason
    about the failing command.
    """

    name: str = ""
    environment: str = ""
    company: str = ""
    auth_method: str = ""
    disable_writes: bool = False


@dataclass(frozen=True)
class HttpEvent:
    """One line from the rolling ``bcli.http`` tail file.

    ``url`` arrives here already URL-scrubbed (query params replaced
    with ``?key=[REDACTED]``). ``correlation_id`` is the BC
    ``x-ms-correlation-request-id`` header, useful for cross-checking
    against an Azure Application Insights record.
    """

    timestamp: str
    method: str
    url: str
    status: int
    latency_ms: float = 0.0
    correlation_id: str = ""
    endpoint: str = ""
    retry_count: int = 0


@dataclass(frozen=True)
class LastErrorRecord:
    """Captured snapshot of the last ``BCLIError`` the CLI raised (R6).

    No traceback by default. ``traceback_excerpt`` is empty unless the
    user ran with ``--debug``; even then it lives in a sibling
    ``last-error-debug.json`` file (mode 0600) that the bundler ignores
    unless ``BundlePolicy.include_debug`` is set.

    Field set mirrors what ``bcli describe`` already serialises for
    error events so an agent reading both sees consistent shapes.
    """

    timestamp: str
    command: str
    error_class: str
    exit_code: int
    status: int = 0
    profile: str = ""
    environment: str = ""
    company: str = ""
    url: str = ""
    method: str = ""
    correlation_id: str = ""
    endpoint: str = ""
    hint: str = ""
    bc_message: str = ""
    traceback_excerpt: str = ""


@dataclass(frozen=True)
class Attachment:
    """A user-supplied ``--attach <path>`` file post-redaction.

    Always re-run through the three-layer redactor before inclusion —
    treat the attachment as untrusted even when the operator brought
    it from their own disk. ``original_bytes`` is the on-disk size;
    ``included_bytes`` is what survived redaction + truncation.
    """

    label: str
    path: str
    content: str
    original_bytes: int
    included_bytes: int


@dataclass(frozen=True)
class ContextBundle:
    """Top-level model-bound context object (R4).

    Constructed by :func:`bcli.context.build_bundle`. Token-budgeted,
    source-attributed, redaction-audited. Consumers (``bcli ask``,
    future ``bcli agent``) call ``to_dict()`` / ``to_prompt_text()``
    to flatten for an HTTP request body.
    """

    schema_version: str = _SCHEMA_VERSION
    generated_at: str = ""
    question: str = ""
    budget: TokenBudget = field(default_factory=TokenBudget)
    policy: BundlePolicy = field(default_factory=BundlePolicy)
    sources: tuple[BundleSource, ...] = ()
    redactions: tuple[RedactionRecord, ...] = ()
    profile_snapshot: ProfileSnapshot = field(default_factory=ProfileSnapshot)
    registry_snapshot_hash: str = ""
    describe_excerpt: str = ""
    recent_http: tuple[HttpEvent, ...] = ()
    last_error: LastErrorRecord | None = None
    attachments: tuple[Attachment, ...] = ()

    def to_prompt_text(self) -> str:
        """Markdown rendering used by ``bcli ask`` prompt assembly.

        Sections appear in priority order — question → last_error →
        profile_snapshot → recent_http → describe_excerpt → attachments.
        Empty sections are skipped. Redaction summary lands as a final
        section so the model can reason about gaps (e.g. "if a header
        looks missing it was redacted by rule X").
        """
        out: list[str] = []
        if self.question:
            out.append("## Question\n")
            out.append(self.question.strip())
            out.append("")
        if self.last_error is not None:
            le = self.last_error
            out.append("## Last error")
            out.append("")
            out.append(f"- class: `{le.error_class}`")
            out.append(f"- exit_code: {le.exit_code}")
            if le.status:
                out.append(f"- http_status: {le.status}")
            if le.command:
                out.append(f"- command: `{le.command}`")
            if le.endpoint:
                out.append(f"- endpoint: `{le.endpoint}`")
            if le.method or le.url:
                out.append(f"- request: `{le.method} {le.url}`".rstrip())
            if le.correlation_id:
                out.append(f"- correlation_id: `{le.correlation_id}`")
            if le.bc_message:
                out.append(f"- bc_message: {le.bc_message}")
            if le.hint:
                out.append(f"- hint: {le.hint}")
            if self.policy.include_debug and le.traceback_excerpt:
                out.append("\n```\n" + le.traceback_excerpt.strip() + "\n```")
            out.append("")
        ps = self.profile_snapshot
        if any((ps.name, ps.environment, ps.company, ps.auth_method)):
            out.append("## Profile")
            out.append("")
            if ps.name:
                out.append(f"- profile: `{ps.name}`")
            if ps.environment:
                out.append(f"- environment: `{ps.environment}`")
            if ps.company:
                out.append(f"- company: `{ps.company}`")
            if ps.auth_method:
                out.append(f"- auth_method: `{ps.auth_method}`")
            if ps.disable_writes:
                out.append("- disable_writes: true")
            out.append("")
        if self.recent_http:
            out.append("## Recent HTTP")
            out.append("")
            for h in self.recent_http:
                out.append(
                    f"- {h.timestamp} {h.method} `{h.url}` → {h.status} "
                    f"({h.latency_ms:.0f}ms)"
                    + (f" corr=`{h.correlation_id}`" if h.correlation_id else "")
                )
            out.append("")
        if self.describe_excerpt:
            out.append("## Describe excerpt")
            out.append("")
            out.append("```json")
            out.append(self.describe_excerpt.strip())
            out.append("```")
            out.append("")
        if self.attachments:
            out.append("## Attachments")
            out.append("")
            for a in self.attachments:
                out.append(f"### `{a.label}` ({a.included_bytes} of "
                           f"{a.original_bytes} bytes after redaction)")
                out.append("")
                out.append("```")
                out.append(a.content)
                out.append("```")
                out.append("")
        if self.redactions:
            rule_counts: dict[str, int] = {}
            for r in self.redactions:
                rule_counts[r.rule_id] = rule_counts.get(r.rule_id, 0) + 1
            out.append("## Redactions applied")
            out.append("")
            for rule, count in sorted(rule_counts.items()):
                out.append(f"- `{rule}`: {count} occurrence(s)")
            out.append("")
        if self.budget.truncated:
            out.append(f"_Bundle truncated to fit {self.budget.max_tokens} "
                       f"token budget (~{self.budget.actual_tokens} actual)._")
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly nested dict; used by ask backends + tests."""
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "question": self.question,
            "budget": {
                "max_tokens": self.budget.max_tokens,
                "actual_tokens": self.budget.actual_tokens,
                "truncated": self.budget.truncated,
            },
            "policy": {
                "include_bodies": self.policy.include_bodies,
                "include_describe": self.policy.include_describe,
                "include_http_tail": self.policy.include_http_tail,
                "include_debug": self.policy.include_debug,
                "redact_company_ids": self.policy.redact_company_ids,
                "attachment_max_bytes": self.policy.attachment_max_bytes,
            },
            "sources": [
                {
                    "kind": s.kind,
                    "label": s.label,
                    "path": s.path,
                    "included_bytes": s.included_bytes,
                }
                for s in self.sources
            ],
            "redactions": [
                {
                    "rule_id": r.rule_id,
                    "location_path": r.location_path,
                    "redacted_length": r.redacted_length,
                }
                for r in self.redactions
            ],
            "profile_snapshot": {
                "name": self.profile_snapshot.name,
                "environment": self.profile_snapshot.environment,
                "company": self.profile_snapshot.company,
                "auth_method": self.profile_snapshot.auth_method,
                "disable_writes": self.profile_snapshot.disable_writes,
            },
            "registry_snapshot_hash": self.registry_snapshot_hash,
            "describe_excerpt": self.describe_excerpt,
            "recent_http": [
                {
                    "timestamp": h.timestamp,
                    "method": h.method,
                    "url": h.url,
                    "status": h.status,
                    "latency_ms": h.latency_ms,
                    "correlation_id": h.correlation_id,
                    "endpoint": h.endpoint,
                    "retry_count": h.retry_count,
                }
                for h in self.recent_http
            ],
            "last_error": (
                None if self.last_error is None
                else {
                    "timestamp": self.last_error.timestamp,
                    "command": self.last_error.command,
                    "error_class": self.last_error.error_class,
                    "exit_code": self.last_error.exit_code,
                    "status": self.last_error.status,
                    "profile": self.last_error.profile,
                    "environment": self.last_error.environment,
                    "company": self.last_error.company,
                    "url": self.last_error.url,
                    "method": self.last_error.method,
                    "correlation_id": self.last_error.correlation_id,
                    "endpoint": self.last_error.endpoint,
                    "hint": self.last_error.hint,
                    "bc_message": self.last_error.bc_message,
                    "traceback_excerpt": self.last_error.traceback_excerpt,
                }
            ),
            "attachments": [
                {
                    "label": a.label,
                    "path": a.path,
                    "content": a.content,
                    "original_bytes": a.original_bytes,
                    "included_bytes": a.included_bytes,
                }
                for a in self.attachments
            ],
        }


__all__ = [
    "Attachment",
    "BundlePolicy",
    "BundleSource",
    "ContextBundle",
    "HttpEvent",
    "LastErrorRecord",
    "ProfileSnapshot",
    "RedactionRecord",
    "TokenBudget",
]
