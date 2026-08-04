"""Tests for bcli.queries catalog loading (YAML file + already-parsed mapping)."""

from __future__ import annotations

import textwrap

import pytest

from bcli.queries import (
    RESERVED_QUERY_NAMES,
    QueryCatalogError,
    load_catalog,
    load_catalog_from_mapping,
    resolve_alias,
    resolve_query_name,
)

# ── load_catalog (YAML file) ────────────────────────────────────────────


def test_load_catalog_missing_file_returns_empty(tmp_path):
    assert load_catalog(tmp_path / "nope.yaml") == {}


def test_load_catalog_parses_valid_file(tmp_path):
    f = tmp_path / "team.yaml"
    f.write_text(
        textwrap.dedent("""\
        queries:
          customer-by-name:
            description: Look up a customer by display name
            endpoint: customers
            params:
              name:
                required: true
            filter: "displayName eq '${{ params.name }}'"
            orderby: displayName asc
            top: 25
        """)
    )
    queries = load_catalog(f)
    assert "customer-by-name" in queries
    spec = queries["customer-by-name"]
    assert spec["endpoint"] == "customers"
    assert spec["top"] == 25


def test_load_catalog_empty_file_returns_empty(tmp_path):
    f = tmp_path / "empty.yaml"
    f.write_text("")
    assert load_catalog(f) == {}


def test_load_catalog_rejects_malformed_yaml(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("queries: [unterminated\n")
    with pytest.raises(QueryCatalogError, match="Failed to parse"):
        load_catalog(f)


def test_load_catalog_rejects_non_mapping_queries(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("queries:\n  - just-a-list-item\n")
    with pytest.raises(QueryCatalogError, match="'queries' must be a mapping"):
        load_catalog(f)


def test_load_catalog_rejects_reserved_names(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("queries:\n  list:\n    endpoint: customers\n")
    with pytest.raises(QueryCatalogError, match="reserved query names"):
        load_catalog(f)


# ── load_catalog_from_mapping ───────────────────────────────────────────


def test_load_catalog_from_mapping_accepts_already_parsed_dict():
    raw = {"queries": {"foo": {"endpoint": "vendors"}}}
    assert load_catalog_from_mapping(raw) == {"foo": {"endpoint": "vendors"}}


def test_load_catalog_from_mapping_none_returns_empty():
    assert load_catalog_from_mapping(None) == {}


def test_load_catalog_from_mapping_error_includes_source_label():
    with pytest.raises(QueryCatalogError, match="my-source"):
        load_catalog_from_mapping({"queries": ["nope"]}, source="my-source")


def test_reserved_query_names_constant():
    assert {"list", "search", "find", "info", "run"} == RESERVED_QUERY_NAMES


# ── resolve_alias / resolve_query_name ──────────────────────────────────


_CATALOG = {
    "overdue-ic": {"aliases": ["overdue-intercompany", "IC-Overdue"]},
    "open-pos": {},
}


def test_resolve_alias_matches_case_insensitively():
    assert resolve_alias(_CATALOG, "ic-overdue") == "overdue-ic"


def test_resolve_alias_no_match_returns_none():
    assert resolve_alias(_CATALOG, "nope") is None


def test_resolve_alias_ignores_non_list_aliases():
    catalog = {"foo": {"aliases": "not-a-list"}}
    assert resolve_alias(catalog, "not-a-list") is None


def test_resolve_query_name_direct_hit():
    assert resolve_query_name(_CATALOG, "open-pos") == "open-pos"


def test_resolve_query_name_via_alias():
    assert resolve_query_name(_CATALOG, "overdue-intercompany") == "overdue-ic"


def test_resolve_query_name_unknown_returns_none():
    assert resolve_query_name(_CATALOG, "unknown-thing") is None
