"""Tests for the saved-query command (bcli q ...)."""

from __future__ import annotations

import textwrap

import pytest
import typer

from bcli_cli.commands.query_cmd import (
    _expand_query,
    _load_saved_queries,
    _resolve_params,
)


# ── _load_saved_queries ───────────────────────────────────────────────────


def test_load_saved_queries_missing_file(tmp_path):
    queries = _load_saved_queries(tmp_path / "nope.yaml")
    assert queries == {}


def test_load_saved_queries_parses_valid_file(tmp_path):
    f = tmp_path / "engine-tech.yaml"
    f.write_text(textwrap.dedent("""\
    queries:
      utilization-by-esn:
        description: Utilization records for an engine
        endpoint: engineUtilizations
        params:
          esn:
            required: true
        filter: "engineSerialNumber eq '${{ params.esn }}'"
        orderby: asOfDate desc
        top: 24
    """))
    queries = _load_saved_queries(f)
    assert "utilization-by-esn" in queries
    spec = queries["utilization-by-esn"]
    assert spec["endpoint"] == "engineUtilizations"
    assert spec["top"] == 24


def test_load_saved_queries_rejects_non_mapping(tmp_path, monkeypatch):
    """If queries: is a list, we exit with a clear error."""
    f = tmp_path / "bad.yaml"
    f.write_text("queries:\n  - just-a-list-item\n")

    with pytest.raises(typer.Exit):
        _load_saved_queries(f)


# ── _resolve_params ───────────────────────────────────────────────────────


def test_resolve_params_uses_default():
    declared = {"top": {"required": False, "default": 10}}
    resolved = _resolve_params(declared, [])
    assert resolved == {"top": 10}


def test_resolve_params_cli_overrides_default():
    declared = {"top": {"required": False, "default": 10}}
    resolved = _resolve_params(declared, ["top=5"])
    assert resolved == {"top": 5}


def test_resolve_params_required_missing_exits():
    declared = {"esn": {"required": True}}
    with pytest.raises(typer.Exit):
        _resolve_params(declared, [])


def test_resolve_params_required_supplied():
    declared = {"esn": {"required": True}}
    resolved = _resolve_params(declared, ["esn=193208"])
    assert resolved == {"esn": 193208}  # YAML coerces digits


def test_resolve_params_string_value():
    resolved = _resolve_params({"airline": {"required": True}}, ["airline=AIRNORTH"])
    assert resolved == {"airline": "AIRNORTH"}


def test_resolve_params_invalid_format_exits():
    with pytest.raises(typer.Exit):
        _resolve_params({}, ["no-equals-sign"])


# ── _expand_query ─────────────────────────────────────────────────────────


def test_expand_query_resolves_param_references():
    spec = {
        "endpoint": "engineUtilizations",
        "filter": "engineSerialNumber eq '${{ params.esn }}'",
        "top": 24,
    }
    expanded = _expand_query(spec, {"esn": "193208"})
    assert expanded["filter"] == "engineSerialNumber eq '193208'"
    assert expanded["endpoint"] == "engineUtilizations"
    assert expanded["top"] == 24


def test_expand_query_preserves_full_reference_type():
    spec = {"endpoint": "x", "top": "${{ params.limit }}"}
    expanded = _expand_query(spec, {"limit": 50})
    # Full-reference resolution preserves the int type.
    assert expanded["top"] == 50
