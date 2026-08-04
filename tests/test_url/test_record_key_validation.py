"""Record keys and entity-set names must not carry raw URL path syntax.

``build_url`` validated the custom-API route segments (publisher/group/version)
but spliced ``entity_set_name`` and ``record_id`` in raw. A record key is
attacker-influenced in any layer that accepts one from a caller, and a raw ``/``
in it starts a new path segment — so the composed URL could address an entity
other than the one the registry was consulted about:

    entity_set_name="engineOverviews"
    record_id="1)/../../../../../../glEntries('X'"
    → .../engineOverviews(1)/../../../../../../glEntries('X')

which collapses to ``glEntries('X')``. The registry only ever saw
``engineOverviews``, so its decision was made about the wrong target. Business
Central still applies the caller's own permission set, so this is not a way to
read data the user could not otherwise reach — but it does defeat the
client-side registry restriction, and ``bcli/client/_async.py``'s bound-action
resolver documents an assumption ("everything from the ``(`` onward is opaque to
the registry ... gated on the parent, which is the security-relevant identity")
that only holds once keys cannot contain path syntax.

The fix rejects raw path and query delimiters. Everything else a real OData key
needs — quotes, commas, equals signs, hyphens, parentheses inside a quoted
string — keeps working, because those cannot start a new path segment.
"""

from __future__ import annotations

import pytest

from bcli._url import build_url

ENV = "SBEnvJun26"
COMPANY = "f99bd320-b400-4189-b3c1-c62c05d4e7a5"


def _build(**kw) -> str:
    return build_url(environment=ENV, company_id=COMPANY, **kw)


class TestRecordIdRejectsPathSyntax:
    def test_rejects_the_reported_traversal_payload(self):
        with pytest.raises(ValueError, match="record_id"):
            _build(
                entity_set_name="engineOverviews",
                record_id="1)/../../../../../../glEntries('X'",
            )

    @pytest.mark.parametrize(
        "bad",
        [
            "1/2",
            "1)/glEntries('X'",
            "..",
            "../customers",
            "1\\2",
            "1)?$filter=true",
            "1)#frag",
        ],
    )
    def test_rejects_path_and_query_delimiters(self, bad: str):
        with pytest.raises(ValueError, match="record_id"):
            _build(entity_set_name="engineOverviews", record_id=bad)

    def test_error_names_percent_encoding_as_the_remedy(self):
        """A caller with a genuine '/' in a key needs to know what to do."""
        with pytest.raises(ValueError, match="percent-encode"):
            _build(entity_set_name="engineOverviews", record_id="AB/CD")


class TestRecordIdStillAcceptsRealKeys:
    @pytest.mark.parametrize(
        "good",
        [
            "f99bd320-b400-4189-b3c1-c62c05d4e7a5",  # GUID
            "42",  # integer key
            "'V00010'",  # quoted string
            "'O''Brien'",  # quoted string, escaped quote
            "'ACME (US)'",  # parens inside a quoted string are legitimate
            "k1='a',k2='b'",  # composite key
            "%2F",  # already percent-encoded separator
        ],
    )
    def test_accepts(self, good: str):
        url = _build(entity_set_name="engineOverviews", record_id=good)
        assert url.endswith(f"engineOverviews({good})")

    def test_none_record_id_is_unchanged(self):
        url = _build(entity_set_name="engineOverviews")
        assert url.endswith("engineOverviews")


class TestEntitySetNameIsValidatedToo:
    @pytest.mark.parametrize("bad", ["a/b", "..", ".", "a\\b"])
    def test_rejects_path_syntax(self, bad: str):
        with pytest.raises(ValueError, match="entity_set_name"):
            _build(entity_set_name=bad)

    def test_accepts_a_normal_entity_set(self):
        assert _build(entity_set_name="engineOverviews").endswith("engineOverviews")


class TestCustomApiRouteStillValidated:
    """Pre-existing behaviour must not regress."""

    def test_publisher_traversal_still_rejected(self):
        with pytest.raises(ValueError, match="publisher"):
            _build(
                entity_set_name="engineOverviews",
                publisher="../..",
                group="technical",
                version="v1.5",
            )

    def test_custom_route_composes(self):
        url = _build(
            entity_set_name="engineOverviews",
            publisher="beautech",
            group="technical",
            version="v1.5",
        )
        assert "/api/beautech/technical/v1.5/" in url


class TestBoundActionKeyIsValidated:
    """The bound-action resolver splices ``(key)/Namespace.Action`` onto a parent
    URL it resolved from the registry. If the key can hold a path separator, the
    parent-only registry check is not the boundary the code says it is."""

    def _client(self):
        from bcli.client._async import AsyncBCClient
        from bcli.config._model import BCConfig, BCProfile

        profile = BCProfile(
            tenant_id="t",
            environment=ENV,
            company_id=COMPANY,
            client_id="c",
            disable_standard_api=True,
        )
        config = BCConfig(profiles={"p": profile})
        config.defaults.profile = "p"
        return AsyncBCClient(profile="p", config=config)

    def test_rejects_a_traversing_key_in_a_bound_action(self):
        client = self._client()
        with pytest.raises(ValueError, match="record key|record_id"):
            client._resolve_url_for_target(
                ENV,
                COMPANY,
                "engineOverviews(1)/../../glEntries('X')/Microsoft.NAV.doThing",
                publisher="beautech",
                group="technical",
                version="v1.5",
            )

    def test_still_resolves_a_legitimate_bound_action(self):
        client = self._client()
        url = client._resolve_url_for_target(
            ENV,
            COMPANY,
            "engineOverviews(f99bd320-b400-4189-b3c1-c62c05d4e7a5)/Microsoft.NAV.updateLlpUtilization",
            publisher="beautech",
            group="technical",
            version="v1.5",
        )
        assert url.endswith(
            "engineOverviews(f99bd320-b400-4189-b3c1-c62c05d4e7a5)"
            "/Microsoft.NAV.updateLlpUtilization"
        )
