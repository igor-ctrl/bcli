"""Regression: ``method: POST`` in a YAML batch step is silently
downgraded to GET.

The workflow schema documents ``action: get|post|patch|delete`` but the
batch runner (``_execute_batch``) reads ``step.get("action") or "get"``
— a step that writes ``method: POST`` instead of ``action: post`` is
silently treated as a GET because ``action`` is absent. The bug surfaces
when users copy-paste OData bound-action examples (where the conventional
HTTP method key is ``method``) into a bcli workflow file.

These tests pin the fix: ``method`` is accepted as an alias for
``action`` (lowercased + normalised), and the schema validator rejects
the *combination* of both keys to keep YAML files unambiguous.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bcli.workflow._models import StepDef, WorkflowDef


class TestMethodKeyAlias:
    def test_method_key_normalises_to_action(self):
        """``method: POST`` (no ``action:``) → ``action == 'post'``."""
        step = StepDef(endpoint="examples", method="POST")
        assert step.action == "post"

    def test_method_key_lowercased(self):
        """Authors write HTTP methods in uppercase (``POST``, ``PATCH``)
        out of habit; the model should normalise to the lowercase form
        the rest of bcli expects."""
        for raw, expected in [
            ("POST", "post"),
            ("Patch", "patch"),
            ("delete", "delete"),
            ("GET", "get"),
        ]:
            step = StepDef(endpoint="examples", method=raw)
            assert step.action == expected

    def test_unknown_method_rejected(self):
        with pytest.raises(ValidationError):
            StepDef(endpoint="examples", method="PUT")

    def test_action_and_method_both_set_rejected(self):
        """An author who sets both ``action:`` and ``method:`` to the
        same value is harmless, but to keep the YAML one-way we reject
        the combination — the author should pick one key."""
        with pytest.raises(ValidationError):
            StepDef(endpoint="examples", action="post", method="POST")

    def test_action_alone_still_works(self):
        """The existing ``action:`` spelling must not regress."""
        step = StepDef(endpoint="examples", action="post")
        assert step.action == "post"

    def test_workflow_def_accepts_method_in_step(self):
        wf = WorkflowDef(
            steps=[{"endpoint": "examples", "method": "POST"}],
        )
        assert wf.steps[0].action == "post"


class TestBatchRunnerHonoursMethodAlias:
    """End-to-end: a YAML step that uses ``method:`` instead of
    ``action:`` must produce the right HTTP verb on the client, not a
    silent GET."""

    def test_method_post_calls_client_post(
        self, tmp_path, monkeypatch,
    ):
        from pathlib import Path
        from unittest.mock import AsyncMock

        from bcli.config._model import BCConfig, BCDefaults, BCProfile
        from bcli_cli._state import state
        from bcli_cli.commands.batch_cmd import run_batch

        # Isolate HOME so the ledger lands in tmp_path.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        cfg = BCConfig(
            defaults=BCDefaults(profile="dev"),
            profiles={
                "dev": BCProfile(
                    tenant_id="t1",
                    environment="Sandbox",
                    company_id="c-1",
                    disable_writes=False,
                ),
            },
        )
        state._config = cfg
        state._registry = None
        state.profile_name = None
        state.dry_run = False
        state.quiet = True

        fake = AsyncMock()
        fake.__aenter__ = AsyncMock(return_value=fake)
        fake.__aexit__ = AsyncMock(return_value=False)
        fake.post = AsyncMock(return_value={"id": "x-1"})
        fake.get = AsyncMock(side_effect=AssertionError(
            "client.get must NOT be called — method: POST should reach "
            "client.post via the action alias."
        ))
        fake._resolve_url = lambda entity, **kw: f"https://x/{entity}"

        monkeypatch.setattr(state, "make_async_client", lambda **_: fake)

        # Non-interactive: bypass the --yes prompt.
        import sys
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        yaml_file = tmp_path / "workflow.yaml"
        yaml_file.write_text(
            "name: method-alias\n"
            "steps:\n"
            "  - name: archive_one\n"
            "    method: POST\n"
            "    endpoint: examples\n"
            "    data: {flag: true}\n",
            encoding="utf-8",
        )

        try:
            run_batch(
                file=yaml_file,
                dry_run=False,
                output=None,
                format=None,
                set_params=None,
                params_file=None,
                yes=True,
                result_out=None,
                result_fd=None,
                progress_fd=None,
            )
        except SystemExit:
            pass
        finally:
            state._config = None
            state._registry = None

        # The fix: client.post is what should have been called. The
        # AsyncMock raises in client.get if the runner downgraded.
        assert fake.post.await_count == 1, (
            f"Expected client.post to be called once, "
            f"got post={fake.post.await_count}, get={fake.get.await_count}"
        )
