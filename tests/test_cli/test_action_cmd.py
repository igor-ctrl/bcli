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
