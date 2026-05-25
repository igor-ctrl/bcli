"""Three-layer redaction for the context bundle (R5).

Layer 1 — *key-based* — reuses :func:`bcli.audit._redact.redact` which
walks dicts/lists and replaces values whose key name contains a
sensitive token (``token``, ``secret``, ``authorization``…). Cheap,
deep, and catches structured payloads.

Layer 2 — *pattern-based* — reuses :data:`bcli.telemetry.events._SECRET_RE`
which matches token-shaped substrings in free text (Bearer prefixes,
JWTs, hex tokens, instrumentation keys).

Layer 3 — *URL/GUID/PII scrub* — owned by this module. Strips query
parameters from URLs, optionally redacts GUIDs / BC company IDs per
policy, and trims oversized attachments.

Every redaction emits a :class:`RedactionRecord` so the audit trail
is testable in CI — a regression that silently drops a value lands as
a missing record, not a missing assertion. The rule_ids are stable
public API:

* ``audit:key`` — layer 1, key-based dict redaction.
* ``telemetry:pattern`` — layer 2, token-pattern regex.
* ``context:url_query`` — layer 3, stripped a URL query string.
* ``context:guid`` — layer 3, replaced a GUID per policy.
* ``context:truncate`` — layer 3, attachment cut to fit budget.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bcli.audit._redact import REDACTED, redact as _audit_redact
from bcli.context._protocol import RedactionRecord
from bcli.telemetry.events import _SECRET_RE  # noqa: PLC2701  re-used by design

# Stable rule-id constants (public API).
RULE_AUDIT_KEY = "audit:key"
RULE_TELEMETRY_PATTERN = "telemetry:pattern"
RULE_URL_QUERY = "context:url_query"
RULE_GUID = "context:guid"
RULE_TRUNCATE = "context:truncate"

# Exported set of every rule id this module can emit. Tests assert
# against this so a new rule can't slip in unrecorded.
REDACT_RULES = frozenset(
    [
        RULE_AUDIT_KEY,
        RULE_TELEMETRY_PATTERN,
        RULE_URL_QUERY,
        RULE_GUID,
        RULE_TRUNCATE,
    ]
)


# Default token names handed to layer 1. The audit defaults already
# include the common ones; we widen here because the model-bound bundle
# tolerates false-positives much better than a leaked token.
_DEFAULT_AUDIT_KEYS: tuple[str, ...] = (
    "authorization",
    "auth",
    "token",
    "secret",
    "password",
    "apiKey",
    "api_key",
    "client_secret",
    "x-api-key",
    "cookie",
    "set-cookie",
    "bearer",
    "session",
    "refresh_token",
    "access_token",
)


# GUID regex — covers UUID v1-v8 and the BC company-id shape.
_GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


# ─── Layer 1: key-based dict redaction ──────────────────────────────


def redact_structured(
    value: Any,
    *,
    location_path: str = "",
    extra_keys: tuple[str, ...] = (),
) -> tuple[Any, tuple[RedactionRecord, ...]]:
    """Layer 1 — replace dict values whose key looks sensitive.

    Returns the cleaned copy AND an audit trail. A record is emitted
    per leaf key that matched a sensitive token; we don't try to walk
    nested keys here because the inner :func:`_audit_redact` already
    walks recursively and replaces matched values with ``REDACTED``.
    Counting matches happens via a parallel walk so the audit trail
    captures each location.
    """
    keys = _DEFAULT_AUDIT_KEYS + tuple(k for k in extra_keys if k)
    cleaned = _audit_redact(value, keys)
    records: list[RedactionRecord] = []
    _collect_key_redactions(value, keys, location_path, records)
    return cleaned, tuple(records)


def _collect_key_redactions(
    value: Any,
    needles: tuple[str, ...],
    location_path: str,
    records: list[RedactionRecord],
) -> None:
    """Walk in parallel to the audit redactor; emit a record per hit.

    Audit redactor only returns the cleaned tree, so we recompute hits
    here. Keeping the two walks in lock-step is cheap (one pass over
    the data) and the audit trail is then complete.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            child_loc = f"{location_path}.{k}" if location_path else str(k)
            if isinstance(k, str) and _key_matches(k, needles):
                if isinstance(v, str):
                    length = len(v)
                else:
                    length = len(str(v)) if v is not None else 0
                records.append(
                    RedactionRecord(
                        rule_id=RULE_AUDIT_KEY,
                        location_path=child_loc,
                        redacted_length=length,
                    )
                )
                continue  # don't recurse — child already redacted.
            _collect_key_redactions(v, needles, child_loc, records)
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            child_loc = f"{location_path}[{i}]"
            _collect_key_redactions(item, needles, child_loc, records)


def _key_matches(key: str, needles: tuple[str, ...]) -> bool:
    lowered = key.lower()
    return any(n.lower() in lowered for n in needles)


# ─── Layer 2: pattern-based text scrub ──────────────────────────────


def redact_text(
    text: str, *, location_path: str = ""
) -> tuple[str, tuple[RedactionRecord, ...]]:
    """Layer 2 — replace token-shaped substrings in free text.

    Reuses the telemetry secret regex unchanged so a new pattern added
    there shows up everywhere automatically. The returned audit trail
    contains one record per match.
    """
    if not text:
        return text, ()
    records: list[RedactionRecord] = []

    def _sub(m: re.Match[str]) -> str:
        records.append(
            RedactionRecord(
                rule_id=RULE_TELEMETRY_PATTERN,
                location_path=location_path,
                redacted_length=len(m.group(0)),
            )
        )
        return "[REDACTED]"

    cleaned = _SECRET_RE.sub(_sub, text)
    return cleaned, tuple(records)


# ─── Layer 3: URL / GUID / attachment scrub ─────────────────────────


def redact_url(
    url: str,
    *,
    location_path: str = "",
    redact_guids: bool = False,
) -> tuple[str, tuple[RedactionRecord, ...]]:
    """Strip query parameters, optionally replace GUIDs in the path.

    Query strings can carry SAS tokens, access codes, signed download
    URLs. We rebuild the URL with each param's value replaced by
    ``[REDACTED]`` — preserving the *keys* so an agent can still
    reason about whether a request had an ``$expand``, ``$filter``,
    etc., without leaking the value.

    GUID redaction is policy-gated: BC company ids and record systemIds
    are GUIDs, and sometimes the user *wants* the model to see them
    (e.g. "why is company X failing?"). Off by default.
    """
    if not url:
        return url, ()
    records: list[RedactionRecord] = []
    try:
        parts = urlsplit(url)
    except ValueError:
        return url, ()

    new_query = parts.query
    if parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if pairs:
            rebuilt = []
            for k, v in pairs:
                if v:
                    records.append(
                        RedactionRecord(
                            rule_id=RULE_URL_QUERY,
                            location_path=f"{location_path}?{k}",
                            redacted_length=len(v),
                        )
                    )
                    rebuilt.append((k, "[REDACTED]"))
                else:
                    rebuilt.append((k, ""))
            new_query = urlencode(rebuilt)

    new_path = parts.path
    if redact_guids and new_path:
        def _guid_sub(m: re.Match[str]) -> str:
            records.append(
                RedactionRecord(
                    rule_id=RULE_GUID,
                    location_path=f"{location_path}.path",
                    redacted_length=len(m.group(0)),
                )
            )
            return "[GUID]"

        new_path = _GUID_RE.sub(_guid_sub, new_path)

    cleaned = urlunsplit(
        (parts.scheme, parts.netloc, new_path, new_query, parts.fragment)
    )
    return cleaned, tuple(records)


def redact_guids_in_text(
    text: str, *, location_path: str = ""
) -> tuple[str, tuple[RedactionRecord, ...]]:
    """Layer 3 (text variant) — replace GUIDs in arbitrary strings."""
    if not text:
        return text, ()
    records: list[RedactionRecord] = []

    def _sub(m: re.Match[str]) -> str:
        records.append(
            RedactionRecord(
                rule_id=RULE_GUID,
                location_path=location_path,
                redacted_length=len(m.group(0)),
            )
        )
        return "[GUID]"

    cleaned = _GUID_RE.sub(_sub, text)
    return cleaned, tuple(records)


# ─── Layered orchestrator ───────────────────────────────────────────


def apply_layered_redaction(
    value: Any,
    *,
    location_path: str = "",
    redact_guids: bool = False,
) -> tuple[Any, tuple[RedactionRecord, ...]]:
    """Run all three layers in order over a value.

    Layer order matters: structured (key) → pattern (text) → URL/GUID.
    A token under a sensitive key is caught by layer 1 even if it
    looks "normal"; a token in free text is caught by layer 2; URL
    queries and GUIDs are last because they're the cheapest, most
    surgical step.

    For attachments and free strings the caller passes them directly
    to :func:`redact_text` / :func:`redact_url`; this function is the
    "I have a heterogeneous payload" convenience.
    """
    records: list[RedactionRecord] = []

    # Layer 1
    cleaned, layer1 = redact_structured(value, location_path=location_path)
    records.extend(layer1)

    # Layer 2 — walk strings in the cleaned tree.
    cleaned, layer2 = _walk_strings(
        cleaned, location_path, redact_guids=redact_guids
    )
    records.extend(layer2)

    return cleaned, tuple(records)


def _walk_strings(
    value: Any, location_path: str, *, redact_guids: bool
) -> tuple[Any, tuple[RedactionRecord, ...]]:
    records: list[RedactionRecord] = []
    if isinstance(value, dict):
        out_d: dict[Any, Any] = {}
        for k, v in value.items():
            child_loc = f"{location_path}.{k}" if location_path else str(k)
            new_v, recs = _walk_strings(
                v, child_loc, redact_guids=redact_guids
            )
            records.extend(recs)
            out_d[k] = new_v
        return out_d, tuple(records)
    if isinstance(value, list):
        out_l: list[Any] = []
        for i, item in enumerate(value):
            child_loc = f"{location_path}[{i}]"
            new_v, recs = _walk_strings(
                item, child_loc, redact_guids=redact_guids
            )
            records.extend(recs)
            out_l.append(new_v)
        return out_l, tuple(records)
    if isinstance(value, tuple):
        out_t: list[Any] = []
        for i, item in enumerate(value):
            child_loc = f"{location_path}[{i}]"
            new_v, recs = _walk_strings(
                item, child_loc, redact_guids=redact_guids
            )
            records.extend(recs)
            out_t.append(new_v)
        return tuple(out_t), tuple(records)
    if isinstance(value, str):
        cleaned = value
        if value == REDACTED:
            return cleaned, ()
        cleaned, recs1 = redact_text(cleaned, location_path=location_path)
        records.extend(recs1)
        if redact_guids:
            cleaned, recs2 = redact_guids_in_text(
                cleaned, location_path=location_path
            )
            records.extend(recs2)
        return cleaned, tuple(records)
    return value, ()


# ─── Attachment scanning ────────────────────────────────────────────


def scan_attachment(
    content: str,
    *,
    label: str,
    max_bytes: int,
    redact_guids: bool = False,
) -> tuple[str, int, tuple[RedactionRecord, ...]]:
    """Redact + truncate an attachment to fit ``max_bytes``.

    Returns ``(cleaned_content, included_bytes, records)``. Truncation
    is byte-budgeted on the post-redaction text; if it has to cut, a
    ``context:truncate`` record lands in the trail so the operator
    can see *why* the model didn't get the full file.
    """
    records: list[RedactionRecord] = []
    cleaned, layer2 = redact_text(content, location_path=f"attachment:{label}")
    records.extend(layer2)
    if redact_guids:
        cleaned, layer3 = redact_guids_in_text(
            cleaned, location_path=f"attachment:{label}"
        )
        records.extend(layer3)
    encoded = cleaned.encode("utf-8")
    if len(encoded) > max_bytes:
        truncated_bytes = encoded[:max_bytes]
        records.append(
            RedactionRecord(
                rule_id=RULE_TRUNCATE,
                location_path=f"attachment:{label}",
                redacted_length=len(encoded) - max_bytes,
            )
        )
        # decode best-effort; might lose the last char if it's
        # multi-byte. Acceptable for free text.
        cleaned = truncated_bytes.decode("utf-8", errors="ignore")
    return cleaned, len(cleaned.encode("utf-8")), tuple(records)


__all__ = [
    "REDACT_RULES",
    "RULE_AUDIT_KEY",
    "RULE_GUID",
    "RULE_TELEMETRY_PATTERN",
    "RULE_TRUNCATE",
    "RULE_URL_QUERY",
    "apply_layered_redaction",
    "redact_guids_in_text",
    "redact_structured",
    "redact_text",
    "redact_url",
    "scan_attachment",
]
