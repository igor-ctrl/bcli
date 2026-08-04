"""Tests for bcli.queries.expand_query and ResolvedQuery."""

from __future__ import annotations

from bcli.odata import Query
from bcli.queries import ResolvedQuery, expand_query

# ── expand_query — ${{ params.X }} resolution ────────────────────────────


def test_expand_query_resolves_param_references():
    spec = {
        "endpoint": "engineUtilizations",
        "filter": "engineSerialNumber eq '${{ params.esn }}'",
        "top": 24,
    }
    resolved = expand_query(spec, {"esn": "193208"})
    assert resolved.filter == "engineSerialNumber eq '193208'"
    assert resolved.endpoint == "engineUtilizations"
    assert resolved.top == 24


def test_expand_query_preserves_full_reference_type():
    spec = {"endpoint": "x", "top": "${{ params.limit }}"}
    resolved = expand_query(spec, {"limit": 50})
    assert resolved.top == 50
    assert isinstance(resolved.top, int)


def test_expand_query_absent_fields_stay_none():
    resolved = expand_query({"endpoint": "x"}, {})
    assert resolved.filter is None
    assert resolved.select is None
    assert resolved.all is None


def test_expand_query_ignores_non_odata_metadata_fields():
    """description/aliases/tags/etc. aren't part of the request spec."""
    spec = {
        "endpoint": "x",
        "description": "irrelevant to expansion",
        "tags": ["a", "b"],
        "params": {"esn": {"required": True}},
    }
    resolved = expand_query(spec, {"esn": "1"})
    assert not hasattr(resolved, "description")
    assert not hasattr(resolved, "tags")


# ── Filter-context OData escaping ────────────────────────────────────────


def test_expand_query_escapes_single_quote_in_filter():
    spec = {"endpoint": "vendors", "filter": "name eq '${{ params.name }}'"}
    resolved = expand_query(spec, {"name": "O'Brien"})
    assert resolved.filter == "name eq 'O''Brien'"


def test_expand_query_neutralises_injection_in_filter():
    spec = {
        "endpoint": "engineUtilizations",
        "filter": "engineSerialNumber eq '${{ params.esn }}'",
    }
    resolved = expand_query(spec, {"esn": "193208' or 1 eq 1--"})
    assert resolved.filter == "engineSerialNumber eq '193208'' or 1 eq 1--'"
    assert resolved.filter.count("'") % 2 == 0


def test_expand_query_does_not_escape_outside_filter():
    spec = {
        "endpoint": "items",
        "filter": "name eq '${{ params.name }}'",
        "select": "${{ params.name }}",
        "orderby": "${{ params.name }} asc",
    }
    resolved = expand_query(spec, {"name": "O'Brien"})
    assert resolved.filter == "name eq 'O''Brien'"
    assert resolved.select == "O'Brien"
    assert resolved.orderby == "O'Brien asc"


def test_expand_query_leaves_filter_alone_when_no_substitution():
    spec = {"endpoint": "x", "filter": "blocked eq false"}
    resolved = expand_query(spec, {})
    assert resolved.filter == "blocked eq false"


def test_expand_query_non_string_param_in_filter_passes_through():
    spec = {"endpoint": "x", "filter": "amount gt ${{ params.threshold }}"}
    resolved = expand_query(spec, {"threshold": 1000})
    assert resolved.filter == "amount gt 1000"


# ── ResolvedQuery.to_query() / all_pages ─────────────────────────────────


def test_to_query_builds_expected_odata_params():
    resolved = ResolvedQuery(
        endpoint="vendors",
        filter="name eq 'Acme'",
        select="no,name",
        expand="ledgerEntries",
        orderby="name asc",
        top=10,
        skip=5,
    )
    query = resolved.to_query()
    assert isinstance(query, Query)
    params = query.to_params()
    assert params["$filter"] == "(name eq 'Acme')"
    assert params["$select"] == "no,name"
    assert params["$expand"] == "ledgerEntries"
    assert params["$orderby"] == "name asc"
    assert params["$top"] == "10"
    assert params["$skip"] == "5"


def test_to_query_splits_comma_lists_and_strips_whitespace():
    resolved = ResolvedQuery(endpoint="x", select=" a , b ,c", expand=" nav1 , nav2")
    query = resolved.to_query()
    assert query._params.selects == ["a", "b", "c"]
    assert query._params.expands == ["nav1", "nav2"]


def test_to_query_empty_when_no_fields_set():
    resolved = ResolvedQuery(endpoint="x")
    assert resolved.to_query().is_empty


def test_to_query_top_zero_is_applied_not_skipped():
    """`is not None` semantics — top=0 is a real value, not "unset"."""
    resolved = ResolvedQuery(endpoint="x", top=0)
    assert resolved.to_query().to_params()["$top"] == "0"


def test_all_pages_defaults_false():
    assert ResolvedQuery(endpoint="x").all_pages is False


def test_all_pages_true_when_all_set():
    assert ResolvedQuery(endpoint="x", all=True).all_pages is True


def test_all_pages_coerces_truthy_values():
    assert ResolvedQuery(endpoint="x", all="yes").all_pages is True
