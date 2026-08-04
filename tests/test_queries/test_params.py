"""Tests for bcli.queries parameter merging + pre-HTTP validation.

Mirrors the coverage that lived in tests/test_cli/test_query_cmd.py before
the extraction, at the SDK boundary: `supplied` is already a typed mapping
(no `key=value` string parsing here — that's the CLI's job).
"""

from __future__ import annotations

import pytest

from bcli.queries import QueryParamError, resolve_params, validate_param

# ── resolve_params ───────────────────────────────────────────────────────


def test_resolve_params_uses_default():
    declared = {"top": {"required": False, "default": 10}}
    assert resolve_params(declared, {}) == {"top": 10}


def test_resolve_params_supplied_overrides_default():
    declared = {"top": {"required": False, "default": 10}}
    assert resolve_params(declared, {"top": 5}) == {"top": 5}


def test_resolve_params_required_missing_raises():
    declared = {"esn": {"required": True}}
    with pytest.raises(QueryParamError) as exc_info:
        resolve_params(declared, {})
    assert exc_info.value.key == "esn"
    assert exc_info.value.kind == "missing_required"
    assert "Missing required parameter 'esn'" in str(exc_info.value)


def test_resolve_params_required_supplied():
    declared = {"esn": {"required": True}}
    assert resolve_params(declared, {"esn": 193208}) == {"esn": 193208}


def test_resolve_params_none_declared_passes_supplied_through_unvalidated():
    """No schema to check against — supplied values are merged as-is."""
    assert resolve_params(None, {"anything": 1}) == {"anything": 1}


def test_resolve_params_none_declared_and_none_supplied_is_empty():
    assert resolve_params(None, None) == {}


def test_resolve_params_unknown_supplied_keys_pass_through():
    """Params not in `declared` aren't validated but do end up in the result."""
    declared = {"esn": {"required": True}}
    resolved = resolve_params(declared, {"esn": "1", "extra": "kept"})
    assert resolved == {"esn": "1", "extra": "kept"}


# ── validate_param via resolve_params (type/pattern/min/max/enum) ────────


class TestParamValidation:
    def test_integer_type_coerces_string(self):
        declared = {"limit": {"required": True, "type": "integer"}}
        resolved = resolve_params(declared, {"limit": "50"})
        assert resolved == {"limit": 50}
        assert isinstance(resolved["limit"], int)

    def test_integer_type_rejects_non_integer(self):
        declared = {"limit": {"required": True, "type": "integer"}}
        with pytest.raises(QueryParamError):
            resolve_params(declared, {"limit": "abc"})

    def test_integer_max_bound_enforced(self):
        declared = {"limit": {"required": True, "type": "integer", "max": 1000}}
        with pytest.raises(QueryParamError, match="exceeds max"):
            resolve_params(declared, {"limit": 99999})

    def test_integer_min_bound_enforced(self):
        declared = {"limit": {"required": True, "type": "integer", "min": 1}}
        with pytest.raises(QueryParamError, match="below min"):
            resolve_params(declared, {"limit": 0})

    def test_integer_within_bounds_accepted(self):
        declared = {"limit": {"required": True, "type": "integer", "min": 1, "max": 100}}
        assert resolve_params(declared, {"limit": 50}) == {"limit": 50}

    def test_string_pattern_accepts_match(self):
        declared = {"airline": {"required": True, "type": "string", "pattern": r"^[A-Z0-9]{2,8}$"}}
        assert resolve_params(declared, {"airline": "AIRNORTH"}) == {"airline": "AIRNORTH"}

    def test_string_pattern_rejects_non_match(self):
        declared = {"airline": {"required": True, "type": "string", "pattern": r"^[A-Z0-9]{2,8}$"}}
        with pytest.raises(QueryParamError):
            resolve_params(declared, {"airline": "little caesars"})

    def test_string_pattern_rejects_injection_attempt(self):
        declared = {"esn": {"required": True, "type": "string", "pattern": r"^\d{4,8}$"}}
        with pytest.raises(QueryParamError):
            resolve_params(declared, {"esn": "193208' or 1 eq 1--"})

    def test_enum_accepts_valid(self):
        declared = {"status": {"required": True, "enum": ["Open", "Posted"]}}
        assert resolve_params(declared, {"status": "Open"}) == {"status": "Open"}

    def test_enum_rejects_invalid(self):
        declared = {"status": {"required": True, "enum": ["Open", "Posted"]}}
        with pytest.raises(QueryParamError, match="not in allowed set"):
            resolve_params(declared, {"status": "Cancelled"})

    def test_enum_declared_as_non_list_is_a_schema_error(self):
        declared = {"status": {"required": True, "enum": "Open"}}
        with pytest.raises(QueryParamError) as exc_info:
            resolve_params(declared, {"status": "Open"})
        assert exc_info.value.kind == "schema"

    def test_unknown_type_is_a_schema_error(self):
        declared = {"x": {"required": True, "type": "uuid"}}
        with pytest.raises(QueryParamError) as exc_info:
            resolve_params(declared, {"x": "anything"})
        assert exc_info.value.kind == "schema"

    def test_pattern_on_non_string_type_is_a_schema_error(self):
        declared = {"x": {"required": True, "type": "integer", "pattern": r"^\d+$"}}
        with pytest.raises(QueryParamError) as exc_info:
            resolve_params(declared, {"x": 5})
        assert exc_info.value.kind == "schema"

    def test_invalid_regex_is_a_schema_error(self):
        declared = {"x": {"required": True, "type": "string", "pattern": "([unterminated"}}
        with pytest.raises(QueryParamError) as exc_info:
            resolve_params(declared, {"x": "anything"})
        assert exc_info.value.kind == "schema"

    def test_no_type_defaults_to_string(self):
        declared = {"name": {"required": True}}
        assert resolve_params(declared, {"name": "Fabrikam"}) == {"name": "Fabrikam"}

    def test_boolean_type_accepts_string_true(self):
        declared = {"all": {"required": True, "type": "boolean"}}
        assert resolve_params(declared, {"all": "true"}) == {"all": True}

    def test_boolean_type_accepts_native_bool(self):
        declared = {"all": {"required": True, "type": "boolean"}}
        assert resolve_params(declared, {"all": False}) == {"all": False}

    def test_boolean_type_rejects_other_values(self):
        declared = {"all": {"required": True, "type": "boolean"}}
        with pytest.raises(QueryParamError):
            resolve_params(declared, {"all": "yes"})

    def test_number_type_coerces(self):
        declared = {"rate": {"required": True, "type": "number"}}
        assert resolve_params(declared, {"rate": "3.14"}) == {"rate": 3.14}

    def test_number_type_rejects_non_numeric(self):
        declared = {"rate": {"required": True, "type": "number"}}
        with pytest.raises(QueryParamError):
            resolve_params(declared, {"rate": "abc"})


# ── validate_param directly ──────────────────────────────────────────────


def test_validate_param_passthrough_when_untyped():
    assert validate_param("k", "raw", {}) == "raw"


def test_validate_param_string_coerces_non_string_values():
    assert validate_param("k", 42, {"type": "string"}) == "42"
