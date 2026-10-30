"""Regression test for ``_compose_rollback_url`` and bound-action endpoints.

When the batch runner records the outcome of a POST step, it tries to
compose a rollback URL (``DELETE <entity>(<id>)``) so a subsequent
``bcli batch rollback`` can undo the create. Bound-action endpoints
have no such inverse — there is no "DELETE the archiving" of
``examples(42)/Microsoft.NAV.archive``. If the action happened to
return a body containing an ``id`` (unusual but legal for OData
actions), naïve URL composition would yield a nonsensical path like
``examples(42)/Microsoft.NAV.archive(new-id-789)``.

The composer must detect bound-action endpoints and return ``None``,
which causes the ledger to record ``rollback_skipped`` instead.
"""

from __future__ import annotations

from bcli.client._async import AsyncBCClient
from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli.commands.batch_cmd import _compose_rollback_url


def _client() -> AsyncBCClient:
    return AsyncBCClient(profile="dev", config=BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={"dev": BCProfile(tenant_id="t1", environment="Sandbox", company_id="c-1")},
    ))


def test_rollback_url_is_none_for_bound_action_with_id_in_result():
    """Even if a bound action returns ``{"id": ...}``, no rollback URL
    should be composed — the action has no inverse."""
    post_result = {"id": "new-id-789"}
    rb_url = _compose_rollback_url(
        _client(), "examples(42)/Microsoft.NAV.archive", post_result,
    )
    assert rb_url is None


def test_rollback_url_is_none_for_unbound_action_with_id_in_result():
    """Same guard applies to unbound (service-root) actions."""
    rb_url = _compose_rollback_url(
        _client(), "Microsoft.NAV.refreshAll", {"id": "irrelevant"},
    )
    assert rb_url is None


def test_rollback_url_normal_post_unchanged():
    """A plain entity-set POST still gets a composed rollback URL."""
    client = _client()
    rb_url = _compose_rollback_url(
        client, "examples", {"id": "new-id-789"},
    )
    assert rb_url == client.resolve("examples", record_id="new-id-789")
    assert rb_url.endswith("/api/v2.0/companies(c-1)/examples(new-id-789)")
