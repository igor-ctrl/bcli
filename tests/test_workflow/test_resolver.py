"""Tests for the workflow template resolution engine."""

from __future__ import annotations

import pytest

from bcli.errors import WorkflowError
from bcli.workflow._models import StepResult, WorkflowContext
from bcli.workflow._resolver import resolve_references


# ─── Helpers ─────────────────────────────────────────────────────────


def _ctx(
    params: dict | None = None,
    steps: dict[str, StepResult] | None = None,
) -> WorkflowContext:
    ctx = WorkflowContext(params=params or {})
    if steps:
        for name, result in steps.items():
            ctx.set_result(name, result)
    return ctx


def _ok_step(name: str, data) -> StepResult:
    return StepResult(name=name, action="post", endpoint="x", status="ok", data=data)


def _failed_step(name: str, error: str) -> StepResult:
    return StepResult(name=name, action="post", endpoint="x", status="error", error=error)


# ─── Param resolution ────────────────────────────────────────────────


class TestParamResolution:
    def test_string_param(self):
        ctx = _ctx(params={"vendor_no": "V00011"})
        assert resolve_references("${{ params.vendor_no }}", ctx) == "V00011"

    def test_numeric_param_type_preserved(self):
        ctx = _ctx(params={"cost": 4500})
        result = resolve_references("${{ params.cost }}", ctx)
        assert result == 4500
        assert isinstance(result, int)

    def test_float_param_type_preserved(self):
        ctx = _ctx(params={"rate": 3.14})
        result = resolve_references("${{ params.rate }}", ctx)
        assert result == 3.14
        assert isinstance(result, float)

    def test_bool_param_type_preserved(self):
        ctx = _ctx(params={"active": True})
        assert resolve_references("${{ params.active }}", ctx) is True

    def test_hyphenated_param_name(self):
        ctx = _ctx(params={"vendor-no": "V00011", "ship-no": "SHIP0045"})
        # Embedded reference with hyphenated key
        assert (
            resolve_references("vendor eq '${{ params.vendor-no }}'", ctx)
            == "vendor eq 'V00011'"
        )
        # Full-string reference with hyphenated key (type preservation path)
        assert resolve_references("${{ params.ship-no }}", ctx) == "SHIP0045"

    def test_mixed_string_interpolation(self):
        ctx = _ctx(params={"vendor_no": "V00011"})
        result = resolve_references("Invoice for ${{ params.vendor_no }}", ctx)
        assert result == "Invoice for V00011"
        assert isinstance(result, str)

    def test_multiple_params_in_string(self):
        ctx = _ctx(params={"a": "hello", "b": "world"})
        result = resolve_references("${{ params.a }} ${{ params.b }}", ctx)
        assert result == "hello world"

    def test_missing_param_raises(self):
        ctx = _ctx(params={})
        with pytest.raises(WorkflowError, match="Parameter 'vendor_no' not provided"):
            resolve_references("${{ params.vendor_no }}", ctx)

    def test_whitespace_in_braces(self):
        ctx = _ctx(params={"x": "val"})
        assert resolve_references("${{  params.x  }}", ctx) == "val"
        assert resolve_references("${{params.x}}", ctx) == "val"


# ─── Step chaining ───────────────────────────────────────────────────


class TestStepChaining:
    def test_post_result_field(self):
        ctx = _ctx(steps={"create_header": _ok_step("create_header", {"no": "PI-001", "id": "abc"})})
        assert resolve_references("${{ steps.create_header.no }}", ctx) == "PI-001"

    def test_get_result_indexed(self):
        ctx = _ctx(steps={"fetch": _ok_step("fetch", [{"id": "a"}, {"id": "b"}])})
        assert resolve_references("${{ steps.fetch.0.id }}", ctx) == "a"
        assert resolve_references("${{ steps.fetch.1.id }}", ctx) == "b"

    def test_get_result_length(self):
        ctx = _ctx(steps={"fetch": _ok_step("fetch", [{"id": "a"}, {"id": "b"}, {"id": "c"}])})
        result = resolve_references("${{ steps.fetch.length }}", ctx)
        assert result == 3
        assert isinstance(result, int)

    def test_nested_field_access(self):
        ctx = _ctx(steps={"s1": _ok_step("s1", {"meta": {"inner": "deep"}})})
        assert resolve_references("${{ steps.s1.meta.inner }}", ctx) == "deep"

    def test_step_data_as_whole(self):
        data = {"no": "PI-001", "amount": 100}
        ctx = _ctx(steps={"s1": _ok_step("s1", data)})
        assert resolve_references("${{ steps.s1 }}", ctx) == data

    def test_undefined_step_raises(self):
        ctx = _ctx(steps={})
        with pytest.raises(WorkflowError, match="undefined step 'missing'"):
            resolve_references("${{ steps.missing.no }}", ctx)

    def test_failed_step_raises(self):
        ctx = _ctx(steps={"bad": _failed_step("bad", "connection timeout")})
        with pytest.raises(WorkflowError, match="failed step 'bad'.*connection timeout"):
            resolve_references("${{ steps.bad.no }}", ctx)

    def test_missing_field_raises_with_available(self):
        ctx = _ctx(steps={"s1": _ok_step("s1", {"no": "PI-001", "id": "abc"})})
        with pytest.raises(WorkflowError, match="no field 'missing'.*Available fields"):
            resolve_references("${{ steps.s1.missing }}", ctx)

    def test_index_out_of_range_raises(self):
        ctx = _ctx(steps={"s1": _ok_step("s1", [{"id": "a"}])})
        with pytest.raises(WorkflowError, match="index 5 out of range"):
            resolve_references("${{ steps.s1.5.id }}", ctx)

    def test_non_numeric_index_on_list_raises(self):
        ctx = _ctx(steps={"s1": _ok_step("s1", [{"id": "a"}])})
        with pytest.raises(WorkflowError, match="expected numeric index"):
            resolve_references("${{ steps.s1.name.id }}", ctx)


# ─── Recursive resolution in structures ──────────────────────────────


class TestRecursiveResolution:
    def test_dict_resolution(self):
        ctx = _ctx(params={"v": "V00011"}, steps={"s1": _ok_step("s1", {"no": "PI-001"})})
        data = {
            "vendorNo": "${{ params.v }}",
            "documentNo": "${{ steps.s1.no }}",
            "static": "unchanged",
        }
        result = resolve_references(data, ctx)
        assert result == {
            "vendorNo": "V00011",
            "documentNo": "PI-001",
            "static": "unchanged",
        }

    def test_nested_dict_resolution(self):
        ctx = _ctx(params={"x": "hello"})
        data = {"outer": {"inner": "${{ params.x }}"}}
        assert resolve_references(data, ctx) == {"outer": {"inner": "hello"}}

    def test_list_resolution(self):
        ctx = _ctx(params={"a": 1, "b": 2})
        data = ["${{ params.a }}", "${{ params.b }}", "literal"]
        assert resolve_references(data, ctx) == [1, 2, "literal"]

    def test_mixed_types_passthrough(self):
        ctx = _ctx()
        data = {"int": 42, "float": 3.14, "bool": True, "none": None, "str": "hello"}
        assert resolve_references(data, ctx) == data

    def test_original_not_mutated(self):
        ctx = _ctx(params={"v": "resolved"})
        original = {"key": "${{ params.v }}", "nested": {"deep": "${{ params.v }}"}}
        resolve_references(original, ctx)
        assert original == {"key": "${{ params.v }}", "nested": {"deep": "${{ params.v }}"}}


# ─── Passthrough (no references) ─────────────────────────────────────


class TestPassthrough:
    def test_plain_string(self):
        ctx = _ctx()
        assert resolve_references("hello world", ctx) == "hello world"

    def test_plain_dict(self):
        ctx = _ctx()
        data = {"a": 1, "b": "two"}
        assert resolve_references(data, ctx) == {"a": 1, "b": "two"}

    def test_none(self):
        ctx = _ctx()
        assert resolve_references(None, ctx) is None

    def test_int(self):
        ctx = _ctx()
        assert resolve_references(42, ctx) == 42
