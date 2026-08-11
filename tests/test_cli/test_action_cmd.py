"""Tests for the ``bcli action`` verb (OData v4 bound action invocation).

The action verb is a thin sugar layer over ``bcli post``. Internally it
composes the bound-action URL string
``<entitySet>(<key>)/<Namespace>.<actionName>`` and forwards it to the
same client.post path. The verb takes care of:

* default namespace ``Microsoft.NAV`` (the BC convention; the registry
  validator itself remains namespace-agnostic).
* ``--namespace`` override for non-BC tenants.
* ``--data`` (JSON literal or @file) for a body, or no flag at all for
  an empty body (matching ``bcli post``). ``--no-data`` is retained as
  an explicit no-op alias; ``--data`` and ``--no-data`` may not both be
  supplied.
* honouring ``--profile``/``--company``/``--publisher``/``--group``/
  ``--version`` overrides via the standard CLI plumbing.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import typer

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.commands import action_cmd


@pytest.fixture
def cli_state():
    cfg = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={
            "dev": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                company_id="c-123",
                disable_writes=False,
            ),
        },
    )
    state._config = cfg
    state._registry = None
    state.profile_name = None
    state.env_override = None
    state.company_override = None
    state.format = "table"
    state.dry_run = False
    state.quiet = True
    yield state
    state._config = None
    state._registry = None
    state.profile_name = None
    state.dry_run = False


@pytest.fixture
def fake_client(monkeypatch):
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.post = AsyncMock(return_value={"result": "ok"})
    c._resolve_url = lambda entity, **kw: f"https://example.test/{entity}"
    monkeypatch.setattr(state, "make_async_client", lambda **_: c)
    return c


@pytest.fixture(autouse=True)
def non_interactive(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


def _run(
    *,
    entity="examples",
    key="42",
    action="archive",
    data='{"flag": true}',
    no_data=False,
    namespace=None,
    yes=True,
    **kwargs,
):
    kwargs.setdefault("format", None)
    kwargs.setdefault("publisher", None)
    kwargs.setdefault("group", None)
    kwargs.setdefault("version", None)
    kwargs.setdefault("result_out", None)
    kwargs.setdefault("result_fd", None)
    kwargs.setdefault("idempotency_key", None)
    kwargs.setdefault("out", None)
    kwargs.setdefault("overwrite", False)
    return action_cmd.action_command(
        entity_set=entity,
        key=key,
        action_name=action,
        data=data,
        no_data=no_data,
        namespace=namespace,
        yes=yes,
        **kwargs,
    )


class TestURLComposition:
    def test_default_namespace_is_microsoft_nav(self, cli_state, fake_client):
        _run()
        endpoint_arg = fake_client.post.await_args.args[0]
        assert endpoint_arg == "examples(42)/Microsoft.NAV.archive"

    def test_namespace_override(self, cli_state, fake_client):
        _run(namespace="Custom.Ns")
        endpoint_arg = fake_client.post.await_args.args[0]
        assert endpoint_arg == "examples(42)/Custom.Ns.archive"

    def test_quoted_string_key_passed_through(self, cli_state, fake_client):
        _run(key="'ALFKI'")
        endpoint_arg = fake_client.post.await_args.args[0]
        assert endpoint_arg == "examples('ALFKI')/Microsoft.NAV.archive"

    def test_action_uses_post_not_get(self, cli_state, fake_client):
        """OData actions are always POST. The verb must NOT call .get,
        even if a future flag suggests otherwise."""
        _run()
        assert fake_client.post.await_count == 1


class TestDataHandling:
    def test_data_json_literal_parsed(self, cli_state, fake_client):
        _run(data='{"a": 1, "b": "two"}')
        body_arg = fake_client.post.await_args.args[1]
        assert body_arg == {"a": 1, "b": "two"}

    def test_data_from_file(self, cli_state, fake_client, tmp_path: Path):
        f = tmp_path / "payload.json"
        f.write_text('{"loaded": "from-file"}', encoding="utf-8")
        _run(data=f"@{f}")
        body_arg = fake_client.post.await_args.args[1]
        assert body_arg == {"loaded": "from-file"}

    def test_no_data_flag_sends_empty_body(self, cli_state, fake_client):
        """Explicit --no-data sends an empty body."""
        _run(data=None, no_data=True)
        body_arg = fake_client.post.await_args.args[1]
        assert body_arg == {}

    def test_neither_flag_defaults_to_empty_body(self, cli_state, fake_client):
        """Omitting both flags is equivalent to --no-data — matches
        ``bcli post`` semantics so AI agents calling
        ``bcli action examples 42 archive`` don't get a BadParameter."""
        _run(data=None, no_data=False)
        body_arg = fake_client.post.await_args.args[1]
        assert body_arg == {}

    def test_data_and_no_data_mutually_exclusive(self, cli_state, fake_client):
        with pytest.raises(typer.BadParameter):
            _run(data='{"x": 1}', no_data=True)

    def test_malformed_inline_json_raises_bad_parameter_not_traceback(
        self, cli_state, fake_client,
    ):
        """Regression: a mangled inline literal used to surface a raw
        json.JSONDecodeError. It must come back as a usage error, and
        the call must never reach the network."""
        with pytest.raises(typer.BadParameter):
            _run(data="{not valid json")
        fake_client.post.assert_not_called()

    def test_bare_file_path_without_at_prefix_hints_the_fix(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        f = tmp_path / "payload.json"
        f.write_text('{"x": 1}', encoding="utf-8")
        with pytest.raises(typer.BadParameter) as exc_info:
            _run(data=str(f))
        assert "looks like a file path" in str(exc_info.value)
        assert f"-d @{f}" in str(exc_info.value)
        fake_client.post.assert_not_called()

    def test_malformed_at_file_names_the_file(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(typer.BadParameter) as exc_info:
            _run(data=f"@{f}")
        assert str(f) in str(exc_info.value)
        fake_client.post.assert_not_called()


class TestProfileOverrides:
    def test_publisher_group_version_forwarded(self, cli_state, fake_client):
        _run(publisher="acme", group="custom", version="v1.0")
        kwargs = fake_client.post.await_args.kwargs
        assert kwargs.get("publisher") == "acme"
        assert kwargs.get("group") == "custom"
        assert kwargs.get("version") == "v1.0"

    def test_idempotency_key_forwarded(self, cli_state, fake_client):
        _run(idempotency_key="k-123")
        kwargs = fake_client.post.await_args.kwargs
        assert kwargs.get("idempotency_key") == "k-123"


class TestOutDecode:
    """``--out`` decodes the action's base64 return value to raw bytes.

    Distinct from ``--result-out``, which writes the JSON envelope *about*
    the invocation. The two compose; neither implies the other.
    """

    def test_base64_payload_decoded_to_file(
        self, cli_state, fake_client, tmp_path: Path, capsys,
    ):
        payload = b"%PDF-1.4\nfake\n%%EOF\n"
        fake_client.post.return_value = {
            "value": base64.b64encode(payload).decode("ascii"),
        }
        dest = tmp_path / "document.pdf"

        _run(out=dest)

        assert dest.read_bytes() == payload
        # The base64 blob must not also land on stdout — --out means "give me
        # the bytes", not "give me the bytes and dump the encoding too".
        stdout = capsys.readouterr().out
        assert base64.b64encode(payload).decode("ascii") not in stdout

    def test_204_no_content_fails_rather_than_writing_an_empty_file(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        fake_client.post.return_value = {}
        dest = tmp_path / "document.pdf"

        with pytest.raises(typer.Exit):
            _run(out=dest)

        assert not dest.exists()

    def test_204_marks_the_envelope_failed(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        """The POST happened; the caller still didn't get what they asked for."""
        fake_client.post.return_value = {}
        envelope = tmp_path / "env.json"

        with pytest.raises(typer.Exit):
            _run(out=tmp_path / "document.pdf", result_out=envelope)

        assert json.loads(envelope.read_text())["status"] == "failed"

    def test_dict_without_value_lists_the_keys(
        self, cli_state, fake_client, tmp_path: Path, capsys,
    ):
        fake_client.post.return_value = {"status": "ok", "recordId": "42"}
        dest = tmp_path / "document.pdf"

        with pytest.raises(typer.Exit):
            _run(out=dest)

        err = capsys.readouterr().err
        assert "recordId" in err and "status" in err
        assert "--result-out" in err
        assert not dest.exists()

    def test_invalid_base64_fails(self, cli_state, fake_client, tmp_path: Path, capsys):
        fake_client.post.return_value = {"value": "not base64 at all !!!"}
        dest = tmp_path / "document.pdf"

        with pytest.raises(typer.Exit):
            _run(out=dest)

        assert "base64" in capsys.readouterr().err
        assert not dest.exists()

    def test_existing_file_refused_before_the_post(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        """An action can mutate BC — the destination check has to come first."""
        dest = tmp_path / "document.pdf"
        dest.write_bytes(b"do not clobber me")

        with pytest.raises(typer.Exit) as exc:
            _run(out=dest)

        assert exc.value.exit_code == 1
        assert fake_client.post.await_count == 0
        assert dest.read_bytes() == b"do not clobber me"

    def test_overwrite_allows_replacing_the_file(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        payload = b"fresh bytes"
        fake_client.post.return_value = {
            "value": base64.b64encode(payload).decode("ascii"),
        }
        dest = tmp_path / "document.pdf"
        dest.write_bytes(b"stale")

        _run(out=dest, overwrite=True)

        assert dest.read_bytes() == payload

    def test_out_and_result_out_compose(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        payload = b"both channels"
        fake_client.post.return_value = {
            "value": base64.b64encode(payload).decode("ascii"),
        }
        dest = tmp_path / "document.pdf"
        envelope = tmp_path / "env.json"

        _run(out=dest, result_out=envelope)

        assert dest.read_bytes() == payload
        assert json.loads(envelope.read_text())["status"] == "succeeded"


class TestEnvelope:
    def test_envelope_written_on_success(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        out = tmp_path / "env.json"
        _run(result_out=out)
        env = json.loads(out.read_text())
        assert env["status"] == "succeeded"
        assert env["method"] == "POST"
        # Envelope's endpoint field captures the synthetic bound-action
        # string so an agent can audit *which* action was invoked.
        assert env["endpoint"].endswith("/Microsoft.NAV.archive")
