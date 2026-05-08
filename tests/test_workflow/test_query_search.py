"""Tests for the saved-query discoverability layer (list / search / info)."""

from __future__ import annotations


from bcli.workflow._query_search import (
    QueryEntry,
    filter_entries,
    normalize_queries,
    search_entries,
)


_SAMPLE = {
    "overdue-ic": {
        "description": "Overdue intercompany invoices for a vendor",
        "aliases": ["overdue-intercompany", "ic-overdue"],
        "tags": ["period-close", "ap", "intercompany"],
        "owner": "finance-ops",
        "freshness": "live",
        "endpoint": "vendorLedgerEntries",
        "params": {"vendor": {"required": True, "hint": "BC Vendor No."}},
        "examples": ["bcli q overdue-ic vendor=ACME-IC"],
    },
    "open-pos": {
        "description": "Open purchase orders by vendor",
        "tags": ["ap", "purchasing"],
        "owner": "finance-ops",
        "freshness": "live",
        "endpoint": "purchaseOrders",
    },
    "engine-by-esn": {
        "description": "Engine record by serial number",
        "tags": ["engine", "ops"],
        "owner": "engine-tech",
        "freshness": "live",
        "endpoint": "enginesView",
    },
}


def test_normalize_handles_missing_metadata():
    bare = {"x": {"endpoint": "foo"}}
    entries = normalize_queries(bare)
    assert len(entries) == 1
    assert entries[0].name == "x"
    assert entries[0].tags == ()
    assert entries[0].aliases == ()


def test_normalize_sorts_alphabetically():
    entries = normalize_queries(_SAMPLE)
    assert [e.name for e in entries] == ["engine-by-esn", "open-pos", "overdue-ic"]


# ─── filter_entries ───────────────────────────────────────────────────


def test_filter_by_tag():
    entries = normalize_queries(_SAMPLE)
    out = filter_entries(entries, tag="period-close")
    assert [e.name for e in out] == ["overdue-ic"]


def test_filter_by_owner():
    entries = normalize_queries(_SAMPLE)
    out = filter_entries(entries, owner="engine-tech")
    assert [e.name for e in out] == ["engine-by-esn"]


def test_filter_combined():
    entries = normalize_queries(_SAMPLE)
    out = filter_entries(entries, tag="ap", owner="finance-ops")
    assert {e.name for e in out} == {"open-pos", "overdue-ic"}


def test_filter_case_insensitive():
    entries = normalize_queries(_SAMPLE)
    assert len(filter_entries(entries, tag="AP")) == 2


# ─── search_entries ───────────────────────────────────────────────────


def test_search_exact_name_wins():
    entries = normalize_queries(_SAMPLE)
    hits = search_entries(entries, "overdue-ic")
    assert hits[0][1].name == "overdue-ic"
    assert hits[0][0] == 100


def test_search_alias_hit():
    entries = normalize_queries(_SAMPLE)
    hits = search_entries(entries, "ic-overdue")
    assert hits[0][1].name == "overdue-ic"
    assert hits[0][0] >= 90


def test_search_description_substring():
    entries = normalize_queries(_SAMPLE)
    hits = search_entries(entries, "intercompany")
    assert any(e.name == "overdue-ic" for _, e in hits)


def test_search_floor_drops_unrelated():
    entries = normalize_queries(_SAMPLE)
    hits = search_entries(entries, "completely unrelated phrase about cats")
    assert hits == []


def test_search_partial_phrase_finds_query():
    entries = normalize_queries(_SAMPLE)
    hits = search_entries(entries, "engine serial")
    assert any(e.name == "engine-by-esn" for _, e in hits)


def test_search_ranks_name_match_above_tag_match():
    """A query whose name contains the term must outrank one that only tags it."""
    entries = normalize_queries(
        {
            "ap-summary": {"description": "x", "tags": []},
            "vendor-balances": {"description": "y", "tags": ["ap"]},
        }
    )
    hits = search_entries(entries, "ap")
    assert hits[0][1].name == "ap-summary"


def test_query_entry_from_raw_handles_string_aliases():
    """Tolerate scalar `aliases: foo` instead of a list — YAML drift is real."""
    entry = QueryEntry.from_raw("x", {"aliases": "only-one"})
    assert entry.aliases == ("only-one",)


def test_query_entry_from_raw_handles_none_fields():
    entry = QueryEntry.from_raw("x", {"description": None, "tags": None, "params": None})
    assert entry.description == ""
    assert entry.tags == ()
    assert entry.params == {}
