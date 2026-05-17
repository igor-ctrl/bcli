"""Phase 5 additive describe fields — ``positionals`` and option ``limits``.

The MCP server (Phase 5) generates its tool list from describe. To do
that faithfully it needs:

* The positional arguments per command (``bcli post <endpoint>`` —
  ``endpoint`` is positional, not an option).
* Safety bounds on flags that the CLI clamps internally (``--top`` on
  ``bcli get`` is hard-capped at 1000 with a default of 50, for
  example).

Both are additive — the existing describe schema is preserved. Older
consumers that ignore these fields keep working.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from bcli_cli.app import app


runner = CliRunner()


def _describe_json() -> dict:
    result = runner.invoke(app, ["describe", "--format", "json"])
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def _find(payload: dict, path: list[str]) -> dict:
    matches = [c for c in payload["commands"] if c["path"] == path]
    assert matches, f"command {path} not in describe output"
    return matches[0]


class TestPositionals:
    def test_post_has_endpoint_positional(self):
        """``bcli post <endpoint> --data ...`` — ``endpoint`` is a
        required positional. Describe must surface that so the MCP tool
        generator can include it in the tool's input schema."""
        payload = _describe_json()
        post = _find(payload, ["post"])
        assert "positionals" in post, post.keys()
        names = [p["name"] for p in post["positionals"]]
        assert "endpoint" in names
        endpoint = next(p for p in post["positionals"] if p["name"] == "endpoint")
        assert endpoint["required"] is True
        assert endpoint["type"] in {"str", "string"}

    def test_patch_has_endpoint_and_record_id(self):
        payload = _describe_json()
        patch = _find(payload, ["patch"])
        names = [p["name"] for p in patch["positionals"]]
        assert names[:2] == ["endpoint", "record_id"]
        assert all(p["required"] for p in patch["positionals"][:2])

    def test_get_has_optional_record_id(self):
        """``bcli get <endpoint>`` vs ``bcli get <endpoint> <id>``. The id is
        positional but optional. Describe records that.

        (The signature names the entity-set positional ``endpoint`` for
        historical reasons — same parameter name as post/patch/delete.)
        """
        payload = _describe_json()
        get_cmd = _find(payload, ["get"])
        positionals = {p["name"]: p for p in get_cmd["positionals"]}
        assert "endpoint" in positionals
        assert positionals["endpoint"]["required"] is True
        if "record_id" in positionals:
            assert positionals["record_id"]["required"] is False

    def test_commands_without_positionals_get_empty_list(self):
        """``bcli company list`` takes no positional args. The field is
        still present (empty) for consistency."""
        payload = _describe_json()
        company_list = _find(payload, ["company", "list"])
        assert company_list.get("positionals", []) == []


class TestRequiredOptions:
    def test_post_data_option_marked_required(self):
        """``bcli post --data ...`` declares ``typer.Option(..., …)`` —
        required. Describe surfaces that as ``"required": true`` on the
        option entry so the MCP generator can propagate to JSON Schema."""
        payload = _describe_json()
        post = _find(payload, ["post"])
        data_opt = next(o for o in post["options"] if o["name"] == "--data")
        assert data_opt.get("required") is True

    def test_optional_options_dont_carry_required_flag(self):
        """An option with a real default (``typer.Option(False, ...)``)
        is NOT required. The flag is omitted, not set to false."""
        payload = _describe_json()
        post = _find(payload, ["post"])
        yes_opt = next(o for o in post["options"] if o["name"] == "--yes")
        assert "required" not in yes_opt or yes_opt["required"] is False


class TestLimits:
    def test_get_top_carries_default_and_max(self):
        """``bcli get --top`` is a safety-sensitive int. We pin the
        default (50 in the CLI) + the hard cap (1000) so an MCP tool
        generated from describe can clamp before issuing the call."""
        payload = _describe_json()
        get_cmd = _find(payload, ["get"])
        top_opt = next(
            (o for o in get_cmd["options"] if o["name"] == "--top"), None,
        )
        assert top_opt is not None
        # ``limits`` is the new optional sub-object.
        limits = top_opt.get("limits", {})
        assert limits.get("default") == 50
        assert limits.get("maximum") == 1000
        # Minimum is whatever the CLI tolerates — 1 in our case.
        assert limits.get("minimum", 1) == 1
