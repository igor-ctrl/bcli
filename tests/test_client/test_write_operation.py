"""The SDK write operation: ``client.plan(...)`` then ``await client.write(plan)``.

Also covers the read-side helpers that share its resolution path:
``client.resolve`` and ``client.pages``.
"""

from __future__ import annotations

import json

import pytest

import bcli.registry._registry as registry_mod
from bcli.client._async import AsyncBCClient
from bcli.config._model import AuditConfig, BCConfig, BCDefaults, BCProfile
from bcli.errors import ServerError
from bcli.odata._query import Query


@pytest.fixture
def client(tmp_path, monkeypatch) -> AsyncBCClient:
    """Profile ``dev`` with one custom route (``engines``) and audit on."""
    monkeypatch.setattr(registry_mod, "REGISTRIES_DIR", tmp_path)
    (tmp_path / "dev.json").write_text(json.dumps({"endpoints": [{
        "entity_set_name": "engines",
        "api_publisher": "beautech", "api_group": "technical", "api_version": "v1.0",
        "supports": ["GET", "POST", "PATCH", "DELETE"],
    }]}))
    config = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={"dev": BCProfile(tenant_id="t1", environment="Sandbox", company_id="c-dev")},
        audit=AuditConfig(enabled=True, path=str(tmp_path / "audit-{profile}.jsonl")),
    )
    return AsyncBCClient(profile="dev", config=config)


def _audit(tmp_path) -> list[dict]:
    path = tmp_path / "audit-dev.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ── plan / write ──────────────────────────────────────────────────────


async def test_plan_resolves_once_and_write_sends_that_url(client, fake_bc, monkeypatch):
    calls = []
    real_resolve = client.resolve
    monkeypatch.setattr(
        client, "resolve", lambda *a, **kw: calls.append(a) or real_resolve(*a, **kw),
    )

    plan = client.plan("PATCH", "engines", record_id="e-1", body={"x": 1}, etag="W/1")
    await client.write(plan)

    assert len(calls) == 1
    assert fake_bc.last.url == plan.url
    assert "/api/beautech/technical/v1.0/" in plan.url
    assert plan.url.endswith("engines(e-1)")
    assert (fake_bc.last.method, fake_bc.last.json_body, fake_bc.last.etag) == (
        "PATCH", {"x": 1}, "W/1",
    )


async def test_write_returns_status_and_correlation_id(client, fake_bc):
    fake_bc.reply({"id": "e-9"})
    fake_bc.status = 201
    fake_bc.correlation_id = "corr-201"

    result = await client.write(client.plan("POST", "engines", body={"esn": "1"}))

    assert result.body == {"id": "e-9"}
    assert result.status == 201
    assert result.correlation_id == "corr-201"
    assert fake_bc.last.etag is None  # POST carries no If-Match


async def test_write_emits_one_completed_audit_entry(client, fake_bc, tmp_path):
    fake_bc.status = 201
    fake_bc.correlation_id = "corr-ok"

    plan = client.plan("POST", "engines", body={"esn": "1"})
    await client.write(plan)

    entries = _audit(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["outcome"] == "completed"
    assert e["status"] == 201
    assert e["correlation_id"] == "corr-ok"
    assert e["resolved_url"] == plan.url
    assert e["method"] == "POST"
    assert e["caller"] == "sdk"


async def test_failed_write_emits_one_failed_audit_entry(client, fake_bc, tmp_path):
    fake_bc.reply(ServerError("boom", status_code=500, correlation_id="corr-fail"))

    with pytest.raises(ServerError):
        await client.write(client.plan("DELETE", "engines", record_id="e-1"))

    entries = _audit(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["outcome"] == "failed"
    assert e["status"] == 500
    assert e["correlation_id"] == "corr-fail"
    assert "boom" in e["error"]


# ── pages / resolve ───────────────────────────────────────────────────


async def test_pages_sends_top_skip_count(client, fake_bc):
    fake_bc.reply({"value": [{"id": "1"}]})

    pages = [p async for p in client.pages("engines", Query().top(5).skip(10).count())]

    assert pages == [[{"id": "1"}]]
    params = fake_bc.last.params
    assert params["$top"] == "5"
    assert params["$skip"] == "10"
    assert params["$count"] == "true"


async def test_pages_company_id_changes_company_in_url(client, fake_bc):
    fake_bc.default = {"value": []}

    [p async for p in client.pages("engines", company_id="c-other")]

    assert "companies(c-other)" in fake_bc.last.url
    assert "c-dev" not in fake_bc.last.url


def test_explicit_route_override_wins_over_registry(client):
    assert "/api/beautech/technical/v1.0/" in client.resolve("engines")

    url = client.resolve("engines", publisher="other", group="grp", version="v2.0")

    assert "/api/other/grp/v2.0/" in url
    assert "beautech" not in url
