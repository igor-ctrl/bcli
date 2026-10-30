"""Integration tests for workflow features in the batch command."""

from __future__ import annotations

import textwrap
from pathlib import Path
import pytest

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli.workflow._models import WorkflowContext
from bcli_cli._state import state
from bcli_cli.commands.batch_cmd import (
    _build_workflow_params,
    _execute_batch,
    _has_references,
    _parse_set_params,
    _smart_parse_value,
)


# ─── Helpers ─────────────────────────────────────────────────────────


@pytest.fixture
def bc(fake_bc):
    """A writable profile whose real client talks to ``fake_bc``."""
    state._config = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={"dev": BCProfile(tenant_id="t1", environment="Sandbox", company_id="c-1")},
    )
    state._registry = None
    state.profile_name = None
    yield fake_bc
    state._config = None
    state._registry = None


def _write_yaml(tmp_path: Path, content: str) -> Path:
    f = tmp_path / "test.yaml"
    f.write_text(textwrap.dedent(content), encoding="utf-8")
    return f


# ─── _smart_parse_value ──────────────────────────────────────────────


class TestSmartParseValue:
    def test_int(self):
        assert _smart_parse_value("4500") == 4500

    def test_float(self):
        assert _smart_parse_value("3.14") == 3.14

    def test_bool_true(self):
        assert _smart_parse_value("true") is True

    def test_bool_false(self):
        assert _smart_parse_value("false") is False

    def test_string(self):
        assert _smart_parse_value("V00011") == "V00011"

    def test_null(self):
        assert _smart_parse_value("null") is None


# ─── _parse_set_params ───────────────────────────────────────────────


class TestParseSetParams:
    def test_basic(self):
        result = _parse_set_params(["vendor_no=V00011", "cost=4500"])
        assert result == {"vendor_no": "V00011", "cost": 4500}

    def test_none(self):
        assert _parse_set_params(None) == {}

    def test_empty_list(self):
        assert _parse_set_params([]) == {}

    def test_value_with_equals(self):
        result = _parse_set_params(["filter=status eq 'Open'"])
        assert result == {"filter": "status eq 'Open'"}


# ─── _has_references ─────────────────────────────────────────────────


class TestHasReferences:
    def test_string_with_ref(self):
        assert _has_references("${{ params.x }}") is True

    def test_string_without_ref(self):
        assert _has_references("plain") is False

    def test_nested_dict(self):
        assert _has_references({"a": {"b": "${{ steps.s1.no }}"}}) is True

    def test_list_with_ref(self):
        assert _has_references(["${{ params.x }}", "plain"]) is True

    def test_no_refs(self):
        assert _has_references({"a": 1, "b": "two"}) is False


# ─── Step chaining in _execute_batch ─────────────────────────────────


class TestExecuteBatchWorkflow:
    @pytest.mark.asyncio
    async def test_post_chain_resolves_step_reference(self, bc):
        """POST step 1 creates a record; step 2 uses its 'no' field."""
        # Step 1: POST returns a header with 'no' field
        bc.reply(
            {"no": "PI-001", "id": "header-1"},
            {"id": "line-1", "documentNo": "PI-001"},
        )

        steps = [
            {
                "name": "create_header",
                "action": "post",
                "endpoint": "purchaseHeaders",
                "data": {"documentType": "Invoice", "buyFromVendorNo": "V00011"},
            },
            {
                "name": "add_line",
                "action": "post",
                "endpoint": "purchaseLines",
                "data": {"documentNo": "${{ steps.create_header.no }}", "quantity": 1},
            },
        ]
        context = WorkflowContext(params={})

        results = await _execute_batch(steps, context=context, output_format=None)

        assert len(results) == 2
        assert results[0]["status"] == "ok"
        assert results[1]["status"] == "ok"

        # Verify second POST was sent with resolved documentNo
        assert bc.sent[1].url.endswith("/purchaseLines")
        assert bc.sent[1].json_body["documentNo"] == "PI-001"

    @pytest.mark.asyncio
    async def test_param_resolution_in_data(self, bc):
        """Runtime params resolve in step data."""
        bc.reply({"id": "new-1"})

        steps = [
            {
                "name": "s1",
                "action": "post",
                "endpoint": "items",
                "data": {"vendorNo": "${{ params.vendor_no }}"},
            },
        ]
        context = WorkflowContext(params={"vendor_no": "V00011"})

        results = await _execute_batch(steps, context=context)

        assert results[0]["status"] == "ok"
        assert bc.last.json_body["vendorNo"] == "V00011"

    @pytest.mark.asyncio
    async def test_get_result_chained(self, bc):
        """GET results can be indexed in subsequent steps."""
        bc.reply({"value": [{"id": "vendor-1", "name": "AAR"}]}, {"id": "new-1"})

        steps = [
            {"name": "fetch_vendor", "action": "get", "endpoint": "vendors", "params": {"top": 1}},
            {
                "name": "create_entry",
                "action": "post",
                "endpoint": "entries",
                "data": {"vendorId": "${{ steps.fetch_vendor.0.id }}"},
            },
        ]
        context = WorkflowContext(params={})

        results = await _execute_batch(steps, context=context)

        assert results[0]["status"] == "ok"
        assert results[1]["status"] == "ok"
        assert [s.method for s in bc.sent] == ["GET", "POST"]
        assert bc.last.json_body["vendorId"] == "vendor-1"

    @pytest.mark.asyncio
    async def test_failed_step_skips_dependents(self, bc):
        """If step 1 fails, step 2 referencing it gets a resolution error."""
        bc.reply(Exception("API error"))

        steps = [
            {"name": "create_header", "action": "post", "endpoint": "items", "data": {"x": 1}},
            {
                "name": "add_line",
                "action": "post",
                "endpoint": "lines",
                "data": {"ref": "${{ steps.create_header.no }}"},
            },
        ]
        context = WorkflowContext(params={})

        results = await _execute_batch(steps, context=context)

        assert results[0]["status"] == "error"
        assert results[1]["status"] == "error"
        assert "failed step" in results[1]["error"]
        assert len(bc.sent) == 1  # the dependent step never reached BC

    @pytest.mark.asyncio
    async def test_no_context_backward_compat(self, bc):
        """Without context (non-workflow mode), batch works as before."""
        bc.reply({"value": [{"id": "1"}]})

        steps = [
            {"action": "get", "endpoint": "items", "params": {}},
        ]

        results = await _execute_batch(steps, context=None)

        assert len(results) == 1
        assert results[0]["status"] == "ok"


# ─── _build_workflow_params ──────────────────────────────────────────


class TestBuildWorkflowParams:
    def test_defaults_from_params_section(self):
        raw = {"params": {"cost": {"default": 4500, "required": False}}}
        result = _build_workflow_params(raw, set_params=None, params_file=None)
        assert result == {"cost": 4500}

    def test_set_overrides_default(self):
        raw = {"params": {"cost": {"default": 4500, "required": False}}}
        result = _build_workflow_params(raw, set_params=["cost=5000"], params_file=None)
        assert result == {"cost": 5000}

    def test_params_file(self, tmp_path):
        raw = {"params": {"vendor_no": {"required": True}}}
        pf = tmp_path / "params.yaml"
        pf.write_text("vendor_no: V00011\n", encoding="utf-8")
        result = _build_workflow_params(raw, set_params=None, params_file=pf)
        assert result == {"vendor_no": "V00011"}

    def test_set_overrides_params_file(self, tmp_path):
        raw = {"params": {"cost": {"default": 100, "required": False}}}
        pf = tmp_path / "params.yaml"
        pf.write_text("cost: 200\n", encoding="utf-8")
        result = _build_workflow_params(raw, set_params=["cost=300"], params_file=pf)
        assert result == {"cost": 300}

    def test_shorthand_param_default(self):
        raw = {"params": {"vendor_no": "V00011"}}
        result = _build_workflow_params(raw, set_params=None, params_file=None)
        assert result == {"vendor_no": "V00011"}

    def test_missing_required_raises(self):
        from click.exceptions import Exit

        raw = {"params": {"vendor_no": {"required": True}}}
        with pytest.raises(Exit):
            _build_workflow_params(raw, set_params=None, params_file=None)
