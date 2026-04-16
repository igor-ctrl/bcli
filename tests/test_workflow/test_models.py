"""Tests for workflow data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bcli.workflow._models import (
    ParamDef,
    StepResult,
    WorkflowContext,
    WorkflowDef,
)


class TestWorkflowDef:
    def test_minimal_valid(self):
        wf = WorkflowDef(steps=[{"endpoint": "items"}])
        assert len(wf.steps) == 1
        assert wf.steps[0].endpoint == "items"
        assert wf.steps[0].action == "get"

    def test_full_definition(self):
        wf = WorkflowDef(
            name="Test Workflow",
            params={
                "vendor_no": ParamDef(description="Vendor", required=True),
                "cost": ParamDef(default=4500, required=False),
            },
            steps=[
                {"name": "s1", "action": "post", "endpoint": "items", "data": {"key": "val"}},
                {"name": "s2", "action": "get", "endpoint": "items", "params": {"top": 5}},
            ],
        )
        assert wf.name == "Test Workflow"
        assert wf.params["vendor_no"].required is True
        assert wf.params["cost"].default == 4500
        assert wf.steps[0].action == "post"
        assert wf.steps[1].params == {"top": 5}

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowDef(steps=[{"endpoint": "items", "action": "PUT"}])

    def test_missing_endpoint_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowDef(steps=[{"action": "get"}])

    def test_no_steps_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowDef(steps=[])


class TestParamDef:
    def test_defaults(self):
        p = ParamDef()
        assert p.description == ""
        assert p.default is None
        assert p.required is True

    def test_with_default_value(self):
        p = ParamDef(default=4500, required=False, description="Cost in USD")
        assert p.default == 4500
        assert p.required is False


class TestStepResult:
    def test_frozen(self):
        r = StepResult(name="s1", action="post", endpoint="items", status="ok", data={"id": "abc"})
        with pytest.raises(AttributeError):
            r.name = "s2"

    def test_default_data(self):
        r = StepResult(name="s1", action="delete", endpoint="items", status="ok")
        assert r.data == {}


class TestWorkflowContext:
    def test_set_and_get_result(self):
        ctx = WorkflowContext()
        result = StepResult(name="s1", action="post", endpoint="x", status="ok", data={"no": "1"})
        ctx.set_result("s1", result)
        assert ctx.get_result("s1") is result

    def test_get_missing_returns_none(self):
        ctx = WorkflowContext()
        assert ctx.get_result("missing") is None

    def test_params(self):
        ctx = WorkflowContext(params={"vendor_no": "V00011"})
        assert ctx.params["vendor_no"] == "V00011"
