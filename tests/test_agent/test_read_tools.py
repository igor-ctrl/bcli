"""Read-tier handlers: registry-backed lookups, no client required."""

from __future__ import annotations

from _helpers import FakeProfile, make_runtime

from bcli.agent.tools._impl import (
    handle_describe,
    handle_endpoint_info,
    handle_endpoint_search,
)


async def test_endpoint_search_returns_matches(fake_registry) -> None:
    runtime = make_runtime(registry=fake_registry)
    result = await handle_endpoint_search(runtime, pattern="vend")
    names = [m["entity_set_name"] for m in result["matches"]]
    assert "vendors" in names


async def test_endpoint_search_no_match_gives_hint(fake_registry) -> None:
    runtime = make_runtime(registry=fake_registry)
    result = await handle_endpoint_search(runtime, pattern="zzz")
    assert result["matches"] == []
    assert "hint" in result


async def test_endpoint_info_unknown_returns_error(fake_registry) -> None:
    runtime = make_runtime(registry=fake_registry)
    result = await handle_endpoint_info(runtime, name="nope")
    assert result["status"] == "error"


async def test_endpoint_info_known(fake_registry) -> None:
    runtime = make_runtime(registry=fake_registry)
    result = await handle_endpoint_info(runtime, name="vendors")
    assert result["entity_set_name"] == "vendors"
    assert result["caution"] == "low"


async def test_describe_reports_constraints(fake_registry) -> None:
    runtime = make_runtime(
        profile=FakeProfile(environment="production", disable_writes=True),
        registry=fake_registry,
    )
    result = await handle_describe(runtime)
    assert result["is_production"] is True
    assert result["constraints"]["disable_writes"] is True
    assert result["endpoint_count"] == 2


async def test_search_without_registry_errors() -> None:
    runtime = make_runtime(registry=None)
    result = await handle_endpoint_search(runtime, pattern="x")
    assert result["status"] == "error"
