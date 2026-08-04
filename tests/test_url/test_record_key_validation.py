"""Record keys and entity-set names must not carry raw URL path syntax.

``build_url`` validated the custom-API route segments (publisher/group/version)
but spliced ``entity_set_name`` and ``record_id`` in raw. A record key is
attacker-influenced in any layer that accepts one from a caller, and a raw ``/``
in it starts a new path segment, so the composed URL could address an entity
other than the one the registry was consulted about:

    entity_set_name="widgets"
    record_id="1)/../../../../../../ledgerEntries('X'"
    → .../widgets(1)/../../../../../../ledgerEntries('X')

which collapses to ``ledgerEntries('X')``. The registry only ever saw
``widgets``, so its decision was made about the wrong target. The server still
applies whatever permissions the caller has, so this is not a way to read data
the user could not otherwise reach — but it does defeat the client-side registry
restriction, and ``bcli/client/_async.py``'s bound-action resolver documents an
assumption ("everything from the ``(`` onward is opaque to the registry ... gated
on the parent, which is the security-relevant identity") that only holds once
keys cannot contain path syntax.

The fix rejects raw path and query delimiters. Everything else a real OData key
needs — quotes, commas, equals signs, hyphens, parentheses inside a quoted
string — keeps working, because those cannot start a new path segment.
"""

from __future__ import annotations

import pytest

from bcli._url import build_url

ENV = "Sandbox"
COMPANY = "abc-123"


def _build(**kw) -> str:
    return build_url(environment=ENV, company_id=COMPANY, **kw)


class TestRecordIdRejectsPathSyntax:
    def test_rejects_the_reported_traversal_payload(self):
        with pytest.raises(ValueError, match="record_id"):
            _build(
                entity_set_name="widgets",
                record_id="1)/../../../../../../ledgerEntries('X'",
            )

    @pytest.mark.parametrize(
        "bad",
        [
            "1/2",
            "1)/ledgerEntries('X'",
            "..",
            "../customers",
            "1\\2",
            "1)?$filter=true",
            "1)#frag",
        ],
    )
    def test_rejects_path_and_query_delimiters(self, bad: str):
        with pytest.raises(ValueError, match="record_id"):
            _build(entity_set_name="widgets", record_id=bad)

    def test_error_names_percent_encoding_as_the_remedy(self):
        """A caller with a genuine '/' in a key needs to know what to do."""
        with pytest.raises(ValueError, match="percent-encode"):
            _build(entity_set_name="widgets", record_id="AB/CD")


class TestRecordIdStillAcceptsRealKeys:
    @pytest.mark.parametrize(
        "good",
        [
            "00000000-0000-0000-0000-000000000001",  # GUID
            "42",  # integer key
            "'ABC-001'",  # quoted string
            "'O''Brien'",  # quoted string, escaped quote
            "'ACME (US)'",  # parens inside a quoted string are legitimate
            "k1='a',k2='b'",  # composite key
            "%2F",  # already percent-encoded separator
        ],
    )
    def test_accepts(self, good: str):
        url = _build(entity_set_name="widgets", record_id=good)
        assert url.endswith(f"widgets({good})")

    def test_none_record_id_is_unchanged(self):
        url = _build(entity_set_name="widgets")
        assert url.endswith("widgets")


class TestEmptyRecordIdIsNotTheSameAsNone:
    """``None`` means "operate on the collection" and is legitimate — ``bcli get
    <entity>`` with no id is a collection read. An *empty* key is different: it
    means the caller meant to address one record and supplied nothing.

    The first version of this validation guarded with ``if record_id:``, so a
    falsy key skipped validation *and* skipped appending the key — silently
    turning a single-record operation into a collection one. ``delete`` and
    ``patch`` take ``record_id`` as a required positional with no default, so
    ``bcli delete widgets ""`` composed a DELETE against the whole entity set.
    Whether the server would honour that is not the point; the client must not
    build it.
    """

    @pytest.mark.parametrize("empty", ["", "   ", "\t", "\n"])
    def test_empty_record_id_is_rejected(self, empty: str):
        with pytest.raises(ValueError, match="must not be empty"):
            _build(entity_set_name="widgets", record_id=empty)

    def test_empty_record_id_does_not_silently_become_a_collection_url(self):
        with pytest.raises(ValueError):
            _build(entity_set_name="widgets", record_id="")

    def test_none_still_means_collection(self):
        url = _build(entity_set_name="widgets", record_id=None)
        assert url.endswith("widgets")
        assert "(" not in url.rsplit("/", 1)[-1]


class TestEntitySetNameIsValidatedToo:
    @pytest.mark.parametrize("bad", ["a/b", "..", ".", "a\\b"])
    def test_rejects_path_syntax(self, bad: str):
        with pytest.raises(ValueError, match="entity_set_name"):
            _build(entity_set_name=bad)

    def test_accepts_a_normal_entity_set(self):
        assert _build(entity_set_name="widgets").endswith("widgets")


class TestCustomApiRouteStillValidated:
    """Pre-existing behaviour must not regress."""

    def test_publisher_traversal_still_rejected(self):
        with pytest.raises(ValueError, match="publisher"):
            _build(
                entity_set_name="widgets",
                publisher="../..",
                group="ops",
                version="v1.0",
            )

    def test_custom_route_composes(self):
        url = _build(
            entity_set_name="widgets",
            publisher="contoso",
            group="ops",
            version="v1.0",
        )
        assert "/api/contoso/ops/v1.0/" in url


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
                "widgets(1)/../../ledgerEntries('X')/Microsoft.NAV.doThing",
                publisher="contoso",
                group="ops",
                version="v1.0",
            )

    def test_still_resolves_a_legitimate_bound_action(self):
        client = self._client()
        url = client._resolve_url_for_target(
            ENV,
            COMPANY,
            "widgets(00000000-0000-0000-0000-000000000001)/Microsoft.NAV.doThing",
            publisher="contoso",
            group="ops",
            version="v1.0",
        )
        assert url.endswith(
            "widgets(00000000-0000-0000-0000-000000000001)/Microsoft.NAV.doThing"
        )
