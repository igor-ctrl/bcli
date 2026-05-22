"""``bcli.context`` — shared model-bound context layer (Part 0 / R1).

This package is the foundation for any bcli feature that ships context
to an LLM. Today's first consumer is ``bcli ask`` (Part 2); tomorrow's
will be ``bcli agent`` (Part 4, deferred). The package is deliberately
*standalone* — it has no consumers in this PR — so its shape can be
audited and exercised before downstream LLM-driven features land.

Public surface
--------------

* :func:`build_bundle` — pure function: read last-error + http-tail +
  profile snapshot + describe excerpt + attachments, run three layers
  of redaction (key-based → token-pattern → URL/GUID), token-budget
  truncate, and return a typed :class:`ContextBundle`.
* :class:`ContextBundle` / supporting dataclasses (see ``_protocol``).
* :func:`capture_last_error` — invoked from the CLI's central error
  handler; writes ``~/.config/bcli/last-error.json``.
* :func:`enable_http_tail` — wire a :class:`RotatingFileHandler` onto
  the ``bcli.http`` logger when ``[context] tail = true``.

Design rules enforced by the package boundary:

* Nothing in here imports from ``bcli_cli`` (CLI -> SDK only).
* Every emitted artefact is JSON-able + size-bounded.
* Every redaction is logged in the bundle's audit trail
  (:class:`RedactionRecord`).
"""

from __future__ import annotations

from bcli.context._bundle import build_bundle
from bcli.context._http_tail import enable_http_tail, http_tail_path, read_http_tail
from bcli.context._last_error import (
    capture_last_error,
    last_error_path,
    read_last_error,
)
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
    REDACT_RULES,
    apply_layered_redaction,
    redact_text,
    redact_url,
)

__all__ = [
    # Dataclasses
    "Attachment",
    "BundlePolicy",
    "BundleSource",
    "ContextBundle",
    "HttpEvent",
    "LastErrorRecord",
    "ProfileSnapshot",
    "RedactionRecord",
    "TokenBudget",
    # Builders / capturers
    "build_bundle",
    "capture_last_error",
    "enable_http_tail",
    # Redaction
    "REDACT_RULES",
    "apply_layered_redaction",
    "redact_text",
    "redact_url",
    # Path helpers (test surfaces)
    "http_tail_path",
    "last_error_path",
    "read_http_tail",
    "read_last_error",
]
