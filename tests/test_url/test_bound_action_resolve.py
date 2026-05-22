"""Tests for OData v4 bound-action URL recognition in the registry resolver.

A URL of the form ``<entitySet>(<key>)/<Namespace>.<Identifier>`` is an
OData v4 *bound action invocation* (one example among many: BC's
``Microsoft.NAV.*`` actions on a per-record entity). The registry
validator must:

1. Recognise the shape and lookup *only the parent entity set* in the
   registry — not the whole literal string (which has never been an
   entity set name).
2. Still enforce ``disable_standard_api`` based on the parent entity
   set's registry status, exactly like a plain ``get`` would.
3. Surface a parent-entity-missing error (not a "whole URL not found"
   error) when the parent isn't registered on a locked-down profile.

The namespace identifier is *agnostic* — these tests deliberately
exercise ``Custom.Ns.doSomething`` alongside ``Microsoft.NAV.archive``
so any hardcoded namespace check would surface as a failure.
"""

from __future__ import annotations

import pytest

from bcli.client._async import AsyncBCClient
from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli.errors import RegistryError
from bcli.registry._schema import EndpointMetadata


def _make_client(
    *,
    disable_standard: bool = False,
    custom_endpoints: list[EndpointMetadata] | None = None,
    env: str = "Production",
    company_id: str = "company-1",
) -> AsyncBCClient:
    """Build a client wired to an in-memory profile + registry."""
    cfg = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={
            "dev": BCProfile(
                tenant_id="t1",
                environment=env,
                company_id=company_id,
                disable_standard_api=disable_standard,
            ),
        },
    )
    client = AsyncBCClient(profile="dev", config=cfg)
    if custom_endpoints:
        for ep in custom_endpoints:
            client._registry._custom[ep.entity_set_name.lower()] = ep
    return client


class TestBoundActionURLRecognition:
    """The resolver should pass bound-action URLs through to the server
    once the parent entity set is registered (or allowed by the standard
    fallback)."""

    def test_bound_action_resolves_against_parent_standard_entity(self):
        """``customers(<key>)/Microsoft.NAV.archive`` — parent is in the
        standard v2.0 registry, so the URL should compose cleanly."""
        client = _make_client(disable_standard=False)
        url = client._resolve_url("customers(abc-123)/Microsoft.NAV.archive")
        assert url.endswith(
            "/companies(company-1)/customers(abc-123)/Microsoft.NAV.archive"
        ), url

    def test_bound_action_resolves_against_parent_custom_entity(self):
        """Custom-route parent entities should keep their custom route
        (publisher/group/version) in front of the bound-action tail."""
        client = _make_client(
            custom_endpoints=[
                EndpointMetadata(
                    entity_set_name="widgets",
                    api_publisher="acme",
                    api_group="custom",
                    api_version="v1.0",
                ),
            ],
        )
        url = client._resolve_url("widgets(42)/Microsoft.NAV.cancel")
        assert "/api/acme/custom/v1.0/" in url
        assert url.endswith("/widgets(42)/Microsoft.NAV.cancel")

    def test_bound_action_namespace_is_agnostic(self):
        """The validator must not hardcode ``Microsoft.NAV`` — any
        dotted qualified identifier should pass."""
        client = _make_client()
        url = client._resolve_url("customers(abc-123)/Custom.Ns.doSomething")
        assert url.endswith("/customers(abc-123)/Custom.Ns.doSomething")

    def test_bound_action_with_quoted_key(self):
        """OData v4 string keys are single-quoted; the validator should
        not interpret the quotes — just pass them through."""
        client = _make_client()
        url = client._resolve_url("customers('ALFKI')/Microsoft.NAV.archive")
        assert url.endswith("/customers('ALFKI')/Microsoft.NAV.archive")

    def test_bound_action_with_composite_key(self):
        """OData v4 supports ``(k1='a',k2='b')`` composite keys — the
        validator should not over-validate the inside of the parens."""
        client = _make_client()
        url = client._resolve_url(
            "items(k1='a',k2='b')/Microsoft.NAV.doStuff"
        )
        assert url.endswith("/items(k1='a',k2='b')/Microsoft.NAV.doStuff")

    def test_bound_action_with_multi_segment_namespace(self):
        """Some namespaces are multi-segment (``Foo.Bar.Baz.action``)
        — the parse should still accept them, since ``Namespace`` is
        explicitly multi-part in the OData v4 spec."""
        client = _make_client()
        url = client._resolve_url("widgets(7)/A.B.C.doStuff")
        assert url.endswith("/widgets(7)/A.B.C.doStuff")


class TestRegistryGateOnBoundActions:
    """The ``disable_standard_api`` profile gate must still apply — but
    it should fire on the *parent entity name*, not on the full URL.
    """

    def test_disable_standard_api_blocks_unregistered_parent(self):
        """Parent entity ``examples`` is not in the standard registry
        and not in the custom registry, and the profile has the standard
        catalogue disabled → reject."""
        client = _make_client(disable_standard=True)
        with pytest.raises(RegistryError) as exc:
            client._resolve_url("examples(99)/Microsoft.NAV.archive")
        # The error message should name the *parent* entity, not the
        # whole bound-action string. This is the actionable signal — an
        # operator needs to know which entry to add to the registry.
        msg = str(exc.value)
        assert "examples" in msg
        # Sanity: the verbose composed string shouldn't dominate the
        # error message — the parent name is what the operator looks up.
        assert "Microsoft.NAV" not in msg or msg.index("examples") < msg.index(
            "Microsoft.NAV"
        )

    def test_disable_standard_api_allows_registered_custom_parent(self):
        """Parent ``widgets`` is in the custom registry → action passes
        through even with the standard catalogue disabled."""
        client = _make_client(
            disable_standard=True,
            custom_endpoints=[
                EndpointMetadata(
                    entity_set_name="widgets",
                    api_publisher="acme",
                    api_group="custom",
                    api_version="v1.0",
                ),
            ],
        )
        url = client._resolve_url("widgets(7)/Custom.Ns.archive")
        assert url.endswith("/widgets(7)/Custom.Ns.archive")


class TestUnboundActionAtServiceRootRejected:
    """An unbound action (``/Microsoft.NAV.refreshAll``) lives at the
    service root and has no parent entity set. The v0.1 implementation
    refuses these explicitly so the user gets a clear "not yet
    supported" error instead of a confusing parent-entity lookup
    failure. See PR docstring — out of scope for this iteration."""

    def test_unbound_action_at_service_root_rejected(self):
        client = _make_client()
        with pytest.raises(RegistryError) as exc:
            client._resolve_url("Microsoft.NAV.refreshAll")
        assert "unbound" in str(exc.value).lower() or "not yet supported" in str(
            exc.value
        ).lower()
