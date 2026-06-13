"""Write-safety seam — enforced inside the tool runtime, not the prompt.

Covers the gate matrix (disable_writes / caution=high / production),
decline → typed refusal, auto-approve, fail-closed with no emitter, and
the plan-mode draft_batch replacement (writes nothing).
"""

from __future__ import annotations

import asyncio

import pytest

from bcli.agent._protocol import AgentEvent
from _helpers import FakeMeta, FakeProfile, FakeRegistry, make_runtime

from bcli.agent.tools._impl import handle_draft_batch, handle_post


async def _collect_and_resolve(runtime, coro, *, approve: bool):
    """Drive a gated handler: capture the approval event, resolve it."""
    events: list[AgentEvent] = []

    async def emit(ev: AgentEvent) -> None:
        events.append(ev)
        if ev.kind == "awaiting_approval":
            # Resolve on the next loop tick so the handler is awaiting.
            asyncio.get_running_loop().call_soon(
                runtime.resolve_approval, ev.approval_id, approve,
            )

    runtime.bind_emitter(emit)
    result = await coro
    runtime.bind_emitter(None)
    return result, events


async def test_readonly_profile_gates_write_and_decline_refuses() -> None:
    runtime = make_runtime(profile=FakeProfile(disable_writes=True))
    result, events = await _collect_and_resolve(
        runtime,
        handle_post(runtime, endpoint="vendors", data='{"name": "Acme"}'),
        approve=False,
    )
    assert any(e.kind == "awaiting_approval" for e in events)
    assert result["status"] == "refused"
    assert "declined" in result["reason"]
    assert "read-only" in result["reason"]


async def test_high_caution_endpoint_gates_write() -> None:
    runtime = make_runtime(registry=FakeRegistry({
        "journalLines": FakeMeta("journalLines", caution="high"),
    }))
    _, events = await _collect_and_resolve(
        runtime,
        handle_post(runtime, endpoint="journalLines", data="{}"),
        approve=False,
    )
    reasons = [e.reason for e in events if e.kind == "awaiting_approval"]
    assert reasons and "caution: high" in reasons[0]


async def test_production_target_gates_write() -> None:
    runtime = make_runtime(profile=FakeProfile(environment="production"))
    assert runtime.is_production is True
    _, events = await _collect_and_resolve(
        runtime,
        handle_post(runtime, endpoint="vendors", data="{}"),
        approve=False,
    )
    reasons = [e.reason for e in events if e.kind == "awaiting_approval"]
    assert reasons and "production" in reasons[0]


async def test_auto_approve_skips_gate_event() -> None:
    runtime = make_runtime(profile=FakeProfile(disable_writes=True), auto_approve=True)
    # No emitter bound, auto_approve=True → gate approves without an event.
    decision = await runtime.gate_write(method="POST", endpoint="vendors")
    assert decision.approved is True


async def test_fail_closed_without_emitter() -> None:
    runtime = make_runtime(profile=FakeProfile(disable_writes=True))
    # Gated write, no emitter, no auto_approve → denied (fail closed).
    decision = await runtime.gate_write(method="POST", endpoint="vendors")
    assert decision.approved is False


async def test_no_gate_reasons_approves_immediately() -> None:
    runtime = make_runtime(profile=FakeProfile(environment="sandbox", disable_writes=False))
    decision = await runtime.gate_write(method="POST", endpoint="vendors")
    assert decision.approved is True
    assert decision.reasons == ()


async def test_invalid_json_body_returns_error_not_raise() -> None:
    runtime = make_runtime()
    result = await handle_post(runtime, endpoint="vendors", data="not json")
    assert result["status"] == "error"
    assert "JSON" in result["message"]


async def test_draft_batch_renders_yaml_and_writes_nothing() -> None:
    runtime = make_runtime(plan_mode=True)
    steps = (
        '[{"name": "create_vendor", "action": "post", "endpoint": "vendors", '
        '"data": {"displayName": "Acme"}}]'
    )
    result = await handle_draft_batch(runtime, name="onboard", steps=steps)
    assert result["status"] == "drafted"
    assert "vendors" in result["batch_yaml"]
    assert "name: onboard" in result["batch_yaml"]


async def test_draft_batch_rejects_non_write_action() -> None:
    runtime = make_runtime(plan_mode=True)
    result = await handle_draft_batch(
        runtime, name="x", steps='[{"action": "get", "endpoint": "vendors"}]',
    )
    assert result["status"] == "error"


def test_safe_context_requires_env_and_company() -> None:
    from bcli.client._safety import SafeContext
    from bcli.errors import SafetyError

    with pytest.raises(SafetyError):
        SafeContext(client=object(), environment="", company_id="c1")
    with pytest.raises(SafetyError):
        SafeContext(client=object(), environment="sandbox", company_id="")
