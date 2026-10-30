"""Write operations: plan once, then execute.

Every mutation — ``bcli post/patch/delete/action``, a ``bcli batch run`` step,
an MCP tool call, ``SafeContext``, or plain SDK ``client.post(...)`` — goes
through the same two steps on :class:`~bcli.client.AsyncBCClient`:

1. ``client.plan(...)`` resolves the URL exactly once and validates the input.
   The plan is what a dry-run previews and what the real run sends, so the
   two cannot disagree.
2. ``client.write(plan)`` sends it and records the outcome in the profile's
   audit log, returning the body together with the HTTP status and BC's
   correlation id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Methods ``plan()`` accepts. Bound actions are POSTs to a composed URL.
WRITE_METHODS = frozenset({"POST", "PATCH", "DELETE"})


@dataclass(frozen=True)
class WritePlan:
    """A fully-resolved mutation, ready to preview or send."""

    method: str
    endpoint: str
    url: str
    environment: str
    company_id: str | None
    record_id: str | None = None
    body: dict[str, Any] | None = None
    etag: str = "*"
    idempotency_key: str | None = None

    def preview(self) -> dict[str, Any]:
        """The stable ``--dry-run`` payload for this plan."""
        payload: dict[str, Any] = {
            "dry_run": True,
            "method": self.method,
            "endpoint": self.endpoint,
            "resolved_url": self.url,
            "environment": self.environment,
            "company_id": self.company_id,
        }
        if self.record_id is not None:
            payload["record_id"] = self.record_id
        if self.body is not None:
            payload["body"] = self.body
        if self.method != "POST":
            payload["etag"] = self.etag
        return payload


@dataclass(frozen=True)
class WriteResult:
    """What came back from sending a :class:`WritePlan`."""

    plan: WritePlan
    body: dict[str, Any]
    status: int
    correlation_id: str | None
    latency_ms: int
