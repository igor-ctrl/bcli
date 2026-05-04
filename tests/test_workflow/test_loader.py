"""Tests for the workflow YAML loader's bool-key rejection."""

from __future__ import annotations

from pathlib import Path

import pytest

from bcli.errors import WorkflowError
from bcli.workflow import load_workflow_yaml


class TestBoolKeyRejection:
    """YAML 1.1 parses bare ``no:`` as ``False``. We reject that.

    Why: BC fields use ``no`` as a string field name (account
    number, line number). Silent coercion produces payloads with
    ``"false"`` keys that fail at BC with confusing errors.
    """

    def test_bare_no_key_rejected(self) -> None:
        yaml_text = """
        steps:
          - action: post
            endpoint: purchaseLines
            data:
              no: "6260-000000-000"
        """
        with pytest.raises(WorkflowError, match="boolean False"):
            load_workflow_yaml(yaml_text)

    def test_bare_yes_key_rejected(self) -> None:
        yaml_text = """
        config:
          yes: 1
        """
        with pytest.raises(WorkflowError, match="boolean True"):
            load_workflow_yaml(yaml_text)

    def test_quoted_no_key_accepted(self) -> None:
        yaml_text = """
        steps:
          - action: post
            endpoint: purchaseLines
            data:
              "no": "6260-000000-000"
        """
        result = load_workflow_yaml(yaml_text)
        assert result["steps"][0]["data"]["no"] == "6260-000000-000"

    def test_no_inside_string_value_accepted(self) -> None:
        """Templates referencing ``no`` field work — the bool trap
        only bites when ``no`` is a *bare YAML key*."""
        yaml_text = """
        steps:
          - action: post
            endpoint: purchaseLines
            data:
              documentNo: "${{ steps.create_header.no }}"
        """
        result = load_workflow_yaml(yaml_text)
        assert (
            result["steps"][0]["data"]["documentNo"]
            == "${{ steps.create_header.no }}"
        )

    def test_error_message_names_path(self) -> None:
        yaml_text = """
        steps:
          - data:
              no: "X"
        """
        with pytest.raises(WorkflowError) as exc_info:
            load_workflow_yaml(yaml_text)
        assert "data" in str(exc_info.value)

    def test_error_message_suggests_fix(self) -> None:
        with pytest.raises(WorkflowError) as exc_info:
            load_workflow_yaml('no: 1')
        assert '"no"' in str(exc_info.value)


class TestLoaderAcceptsValidYaml:
    """Sanity: the loader is permissive for everything that's not
    a bool-key trap. It is a thin wrapper around ``yaml.safe_load``."""

    def test_load_from_path(self, tmp_path: Path) -> None:
        f = tmp_path / "wf.yaml"
        f.write_text("steps:\n  - action: get\n    endpoint: customers\n")
        result = load_workflow_yaml(f)
        assert result["steps"][0]["endpoint"] == "customers"

    def test_load_empty_yaml_returns_none(self) -> None:
        assert load_workflow_yaml("") is None

    def test_load_real_example(self) -> None:
        """The shipped example must round-trip cleanly through the
        loader. This catches future YAML bool-key regressions in
        examples/."""
        repo_root = Path(__file__).resolve().parents[2]
        example = repo_root / "examples" / "create-purchase-invoice.yaml"
        result = load_workflow_yaml(example)
        # The fix: "no" is a string key with the GL account
        post_lines = [
            step
            for step in result["steps"]
            if step.get("endpoint") == "purchaseLines"
        ]
        assert post_lines, "example should have purchaseLines steps"
        assert post_lines[0]["data"]["no"] == "6260-000000-000"
