"""AgentRuntime — the per-session execution context shared by tool handlers.

Carries the live :class:`~bcli.client._async.AsyncBCClient`, the resolved
profile, the endpoint registry, and the write-safety approval seam. Tool
implementations (:mod:`bcli.agent.tools._impl`) receive the runtime as
their first argument — they never touch CLI state or global singletons,
which keeps the SDK/CLI split intact and the handlers testable with a
fake client.

The approval seam
-----------------

Write handlers call :meth:`AgentRuntime.gate_write`. When the gate fires
(``disable_writes``, ``caution == "high"``, or a production target) an
``awaiting_approval`` :class:`~bcli.agent._protocol.AgentEvent` is emitted
through the bound emitter and the handler awaits an :class:`asyncio.Future`.
The consumer (Textual approval dialog, ``/yes``, or the headless prompt)
resolves it via :meth:`resolve_approval`. A decline returns a typed refusal
the model sees — safety is enforced *inside the tool implementations*,
never only in the prompt.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bcli.agent._protocol import AgentEvent

if TYPE_CHECKING:
    from bcli.client._async import AsyncBCClient
    from bcli.client._safety import SafeContext
    from bcli.config._model import BCProfile
    from bcli.registry._registry import EndpointRegistry


_PRODUCTION_NAMES = ("production", "prod")


@dataclass(frozen=True)
class WriteGateDecision:
    """Outcome of :meth:`AgentRuntime.gate_write`."""

    approved: bool
    reasons: tuple[str, ...] = ()

    def refusal(self) -> dict[str, Any]:
        """Typed refusal payload returned to the model on decline."""
        return {
            "status": "refused",
            "reason": (
                "The operator declined this write ("
                + "; ".join(self.reasons)
                + "). Do not retry. Explain what was attempted and ask "
                "the operator how to proceed."
            ),
        }


class AgentRuntime:
    """Execution context handed to every tool handler.

    ``auto_approve=True`` (the headless ``--yes`` path) resolves every
    gate without emitting an approval event. With no emitter bound and
    no auto-approve, gated writes are *denied* — fail closed.
    """

    def __init__(
        self,
        *,
        client: "AsyncBCClient",
        profile: "BCProfile",
        profile_name: str = "",
        registry: "EndpointRegistry | None" = None,
        plan_mode: bool = False,
        auto_approve: bool = False,
    ) -> None:
        self.client = client
        self.profile = profile
        self.profile_name = profile_name
        self.registry = registry
        self.plan_mode = plan_mode
        self.auto_approve = auto_approve
        self._emit: Callable[[AgentEvent], Awaitable[None]] | None = None
        self._pending: dict[str, asyncio.Future[bool]] = {}

    # ── event plumbing ────────────────────────────────────────────────

    def bind_emitter(
        self, emit: Callable[[AgentEvent], Awaitable[None]] | None,
    ) -> None:
        """Backends bind their event queue here at send() time."""
        self._emit = emit

    async def emit(self, event: AgentEvent) -> None:
        if self._emit is not None:
            await self._emit(event)

    # ── write safety ──────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        env = (self.profile.environment or "").lower()
        return env in _PRODUCTION_NAMES

    def caution_for(self, endpoint: str) -> str:
        """Endpoint caution level from the registry (``low`` when unknown)."""
        if self.registry is None:
            return "low"
        meta = self.registry.get(endpoint)
        return getattr(meta, "caution", "low") if meta is not None else "low"

    def domain_for(self, endpoint: str) -> str:
        """Endpoint domain tag from the registry (``standard`` when unknown)."""
        if self.registry is None:
            return "standard"
        meta = self.registry.get(endpoint)
        return getattr(meta, "domain", "standard") if meta is not None else "standard"

    def write_gate_reasons(self, *, endpoint: str) -> tuple[str, ...]:
        """Why a write to ``endpoint`` needs human approval (empty = none)."""
        reasons: list[str] = []
        if getattr(self.profile, "disable_writes", False):
            reasons.append(
                f"profile '{self.profile_name or 'active'}' is read-only "
                "(disable_writes = true)"
            )
        if self.caution_for(endpoint) == "high":
            reasons.append(f"endpoint '{endpoint}' is tagged caution: high")
        if self.is_production:
            reasons.append(
                f"target environment '{self.profile.environment}' is production"
            )
        return tuple(reasons)

    async def gate_write(
        self,
        *,
        method: str,
        endpoint: str,
        payload: Any = None,
    ) -> WriteGateDecision:
        """Run the write-safety gate for one mutating tool call.

        No gate reasons → approved immediately. Otherwise emit
        ``awaiting_approval`` and wait for :meth:`resolve_approval`.
        """
        reasons = self.write_gate_reasons(endpoint=endpoint)
        if not reasons:
            return WriteGateDecision(approved=True)
        if self.auto_approve:
            return WriteGateDecision(approved=True, reasons=reasons)
        if self._emit is None:
            # Fail closed: no human is listening.
            return WriteGateDecision(approved=False, reasons=reasons)

        approval_id = uuid.uuid4().hex
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = fut
        try:
            await self.emit(AgentEvent(
                kind="awaiting_approval",
                approval_id=approval_id,
                tool_name=method,
                tool_args={"endpoint": endpoint, "payload": payload},
                reason="; ".join(reasons),
            ))
            approved = await fut
        finally:
            self._pending.pop(approval_id, None)
        return WriteGateDecision(approved=approved, reasons=reasons)

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        """Resolve a pending approval. Returns ``False`` if unknown/expired."""
        fut = self._pending.get(approval_id)
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        return True

    def pending_approvals(self) -> list[str]:
        return [k for k, f in self._pending.items() if not f.done()]

    # ── write execution ───────────────────────────────────────────────

    def safe_context(self, company: str | None = None) -> "SafeContext":
        """Build a :class:`SafeContext` bound to the resolved env + company.

        ``confirm_production=True`` is intentional here: the *human*
        confirmation already happened through the approval gate (or
        ``auto_approve``); SafeContext re-asserts the explicit env +
        company invariant on every write.
        """
        from bcli.client._safety import SafeContext

        company_id, _ = self.profile.resolve_company(company)
        return SafeContext(
            client=self.client,
            environment=self.profile.environment,
            company_id=company_id,
            confirm_production=True,
        )


__all__ = ["AgentRuntime", "WriteGateDecision"]
