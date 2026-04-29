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
    f = tmp_path / "team.yaml"
    f.write_text(textwrap.dedent("""\
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
    """))
    queries = _load_saved_queries(f)
    assert "customer-by-name" in queries
    spec = queries["customer-by-name"]
    assert spec["endpoint"] == "customers"
    assert spec["top"] == 25


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


# ── Filter-context OData escape (M2 from the security review) ─────────────


def test_expand_query_escapes_single_quote_in_filter():
    """A ``'`` in a string param must be doubled when interpolated into filter."""
    spec = {
        "endpoint": "vendors",
        "filter": "name eq '${{ params.name }}'",
    }
    expanded = _expand_query(spec, {"name": "O'Brien"})
    assert expanded["filter"] == "name eq 'O''Brien'"


def test_expand_query_neutralises_injection_in_filter():
    """The example from the security review must no longer break out."""
    spec = {
        "endpoint": "engineUtilizations",
        "filter": "engineSerialNumber eq '${{ params.esn }}'",
    }
    expanded = _expand_query(spec, {"esn": "193208' or 1 eq 1--"})
    # The injected quote is doubled, so the literal stays well-formed and the
    # ``or 1 eq 1--`` ends up inside the string instead of as new operators.
    assert expanded["filter"] == "engineSerialNumber eq '193208'' or 1 eq 1--'"
    assert expanded["filter"].count("'") % 2 == 0


def test_expand_query_does_not_escape_outside_filter():
    """``select``, ``orderby``, ``top`` etc. must keep raw param values."""
    spec = {
        "endpoint": "items",
        "filter": "name eq '${{ params.name }}'",
        "select": "${{ params.name }}",
        "orderby": "${{ params.name }} asc",
    }
    expanded = _expand_query(spec, {"name": "O'Brien"})
    # Filter is escaped, but select/orderby are not — they don't sit inside
    # an OData string literal, so a raw apostrophe is correct there.
    assert expanded["filter"] == "name eq 'O''Brien'"
    assert expanded["select"] == "O'Brien"
    assert expanded["orderby"] == "O'Brien asc"


def test_expand_query_leaves_filter_alone_when_no_substitution():
    """A filter with no ``${{`` is passed through as-is."""
    spec = {"endpoint": "x", "filter": "blocked eq false"}
    expanded = _expand_query(spec, {})
    assert expanded["filter"] == "blocked eq false"


def test_expand_query_non_string_param_in_filter_passes_through():
    """Numeric params don't get string-escaped."""
    spec = {"endpoint": "x", "filter": "amount gt ${{ params.threshold }}"}
    expanded = _expand_query(spec, {"threshold": 1000})
    assert expanded["filter"] == "amount gt 1000"


# ── Per-param validation (M2 from the security review) ───────────────────


class TestParamValidation:
    def test_integer_type_coerces_string(self):
        declared = {"limit": {"required": True, "type": "integer"}}
        resolved = _resolve_params(declared, ["limit=50"])
        assert resolved == {"limit": 50}
        assert isinstance(resolved["limit"], int)

    def test_integer_type_rejects_non_integer(self):
        declared = {"limit": {"required": True, "type": "integer"}}
        with pytest.raises(typer.Exit):
            _resolve_params(declared, ["limit=abc"])

    def test_integer_max_bound_enforced(self):
        declared = {"limit": {"required": True, "type": "integer", "max": 1000}}
        with pytest.raises(typer.Exit):
            _resolve_params(declared, ["limit=99999"])

    def test_integer_min_bound_enforced(self):
        declared = {"limit": {"required": True, "type": "integer", "min": 1}}
        with pytest.raises(typer.Exit):
            _resolve_params(declared, ["limit=0"])

    def test_integer_within_bounds_accepted(self):
        declared = {"limit": {"required": True, "type": "integer", "min": 1, "max": 100}}
        resolved = _resolve_params(declared, ["limit=50"])
        assert resolved == {"limit": 50}

    def test_string_pattern_accepts_match(self):
        declared = {"airline": {"required": True, "type": "string", "pattern": r"^[A-Z0-9]{2,8}$"}}
        resolved = _resolve_params(declared, ["airline=AIRNORTH"])
        assert resolved == {"airline": "AIRNORTH"}

    def test_string_pattern_rejects_non_match(self):
        declared = {"airline": {"required": True, "type": "string", "pattern": r"^[A-Z0-9]{2,8}$"}}
        with pytest.raises(typer.Exit):
            _resolve_params(declared, ["airline=little caesars"])

    def test_string_pattern_rejects_injection_attempt(self):
        """The injection example from the review fails the ESN pattern check."""
        declared = {"esn": {"required": True, "type": "string", "pattern": r"^\d{4,8}$"}}
        with pytest.raises(typer.Exit):
            _resolve_params(declared, ["esn=193208' or 1 eq 1--"])

    def test_enum_accepts_valid(self):
        declared = {"status": {"required": True, "enum": ["Open", "Posted"]}}
        resolved = _resolve_params(declared, ["status=Open"])
        assert resolved == {"status": "Open"}

    def test_enum_rejects_invalid(self):
        declared = {"status": {"required": True, "enum": ["Open", "Posted"]}}
        with pytest.raises(typer.Exit):
            _resolve_params(declared, ["status=Cancelled"])

    def test_unknown_type_is_a_schema_error(self):
        declared = {"x": {"required": True, "type": "uuid"}}
        with pytest.raises(typer.Exit):
            _resolve_params(declared, ["x=anything"])

    def test_no_type_defaults_to_string(self):
        """Backwards-compat: untyped params still work as strings."""
        declared = {"name": {"required": True}}
        resolved = _resolve_params(declared, ["name=Fabrikam"])
        assert resolved == {"name": "Fabrikam"}

    def test_boolean_type_accepts_string_true(self):
        declared = {"all": {"required": True, "type": "boolean"}}
        resolved = _resolve_params(declared, ["all=true"])
        assert resolved == {"all": True}

    def test_number_type_coerces(self):
        declared = {"rate": {"required": True, "type": "number"}}
        resolved = _resolve_params(declared, ["rate=3.14"])
        assert resolved == {"rate": 3.14}
