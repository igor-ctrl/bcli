"""``bcli get --company all`` and ``--all`` resolve and page through the client.

Both used to rebuild URLs or queries by hand: ``--company all`` let the
registry route override an explicit ``--publisher`` and fetched only the first
page; ``--all`` silently dropped ``--top`` / ``--skip`` / ``--count``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import bcli.registry._registry as registry_mod
from bcli.config._model import BCConfig, BCDefaults, BCProfile, CompanyAlias
from bcli.odata._query import Query
from bcli_cli._state import state
from bcli_cli.commands.get_cmd import _execute_get, _execute_get_all_companies


@pytest.fixture
def cli_state(tmp_path, monkeypatch):
    # One custom endpoint, routed through the registry.
    monkeypatch.setattr(registry_mod, "REGISTRIES_DIR", tmp_path)
    (tmp_path / "dev.json").write_text(json.dumps({"endpoints": [{
        "entity_set_name": "engines",
        "api_publisher": "beautech", "api_group": "technical", "api_version": "v1.0",
    }]}))
    state._config = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={"dev": BCProfile(
            tenant_id="t1", environment="Sandbox", company_id="c-default",
            companies={"LLC": CompanyAlias(id="c-llc", name="LLC"),
                       "GMBH": CompanyAlias(id="c-gmbh", name="GmbH")},
        )},
    )
    state._registry = None
    state.profile_name = None
    state.env_override = None
    state.company_override = None
    yield state
    state._config = None
    state._registry = None


def _pages(fake_bc, *pages):
    """Queue paged replies: every page but the last carries a nextLink."""
    for i, records in enumerate(pages):
        body = {"value": records}
        if i < len(pages) - 1:
            body["@odata.nextLink"] = "https://api.businesscentral.dynamics.com/next"
        fake_bc.reply(body)


def test_company_all_queries_each_company_on_the_registry_route(cli_state, fake_bc):
    fake_bc.reply(*({"value": [{"no": str(i)}]} for i in range(3)))
    records = asyncio.run(_execute_get_all_companies("engines", Query(), None))

    urls = [s.url for s in fake_bc.sent]
    assert len(urls) == 3  # LLC, GMBH, default
    assert all("/api/beautech/technical/v1.0/" in u for u in urls)
    assert {u.split("companies(")[1].split(")")[0] for u in urls} == {"c-llc", "c-gmbh", "c-default"}
    assert {r["_company"] for r in records} == {"LLC", "GMBH", "default"}


def test_company_all_explicit_route_wins_over_registry(cli_state, fake_bc):
    fake_bc.default = {"value": []}
    asyncio.run(_execute_get_all_companies(
        "engines", Query(), None, publisher="other", group="grp", version="v2.0",
    ))
    assert all("/api/other/grp/v2.0/" in s.url for s in fake_bc.sent)


def test_company_all_without_all_reads_first_page_only(cli_state, fake_bc):
    state._config.profiles["dev"].companies = {}
    _pages(fake_bc, [{"no": "1"}], [{"no": "2"}])
    records = asyncio.run(_execute_get_all_companies("engines", Query(), None))
    assert [r["no"] for r in records] == ["1"]


def test_company_all_with_all_follows_next_link(cli_state, fake_bc):
    state._config.profiles["dev"].companies = {}
    _pages(fake_bc, [{"no": "1"}], [{"no": "2"}])
    records = asyncio.run(_execute_get_all_companies("engines", Query(), None, all_pages=True))
    assert [r["no"] for r in records] == ["1", "2"]


def test_all_pages_keeps_top_skip_and_count(cli_state, fake_bc):
    fake_bc.default = {"value": [{"no": "1"}]}
    query = Query().filter("no eq '1'").top(5).skip(10).count()
    asyncio.run(_execute_get("engines", None, query, True))
    params = fake_bc.sent[0].params
    assert params["$top"] == "5"
    assert params["$skip"] == "10"
    assert params["$count"] == "true"
    assert "no eq '1'" in params["$filter"]
