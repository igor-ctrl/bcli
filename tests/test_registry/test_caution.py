"""Tests for the EndpointMetadata.caution flag and verb-name heuristic.

The flag tells agents (and humans driving in autocomplete-y ways) which
endpoints are likely to mutate posted/closed records and should be treated
with extra care. Default is ``low``. The heuristic infers ``high`` from
endpoint names containing common BC mutation verbs (post, release, cancel,
void, reverse, apply, unapply). Importers can also set ``caution`` directly
in JSON, in which case the explicit value wins over the heuristic.
"""

from __future__ import annotations

import pytest

from bcli.registry._importers import _infer_caution
from bcli.registry._schema import EndpointMetadata


def test_caution_defaults_to_low() -> None:
    meta = EndpointMetadata(entity_set_name="customers")
    assert meta.caution == "low"


def test_caution_round_trips_through_schema() -> None:
    meta = EndpointMetadata(entity_set_name="salesInvoicePost", caution="high")
    payload = meta.model_dump()
    assert payload["caution"] == "high"
    restored = EndpointMetadata.model_validate(payload)
    assert restored.caution == "high"


def test_caution_accepts_low_medium_high() -> None:
    for level in ("low", "medium", "high"):
        meta = EndpointMetadata(entity_set_name="x", caution=level)
        assert meta.caution == level


def test_caution_rejects_unknown_levels() -> None:
    with pytest.raises(ValueError):
        EndpointMetadata(entity_set_name="x", caution="extreme")


@pytest.mark.parametrize(
    "name",
    [
        "salesInvoicePost",
        "purchaseInvoicePost",
        "salesOrderRelease",
        "salesOrderCancel",
        "journalVoid",
        "paymentReverse",
        "customerLedgerEntryApply",
        "customerLedgerEntryUnapply",
        # case-insensitive: still high
        "SALESINVOICEPOST",
        "salesinvoiceCANCEL",
    ],
)
def test_infer_caution_high_for_mutation_verbs(name: str) -> None:
    assert _infer_caution(name) == "high"


@pytest.mark.parametrize(
    "name",
    [
        "customers",
        "items",
        "vendors",
        "salesInvoices",
        "purchaseInvoices",
        "companies",
        "currencies",
    ],
)
def test_infer_caution_low_for_plain_entities(name: str) -> None:
    assert _infer_caution(name) == "low"


@pytest.mark.parametrize(
    "name",
    [
        # "post" inside another word boundary should NOT trip — "postal"
        # is not a mutation verb.
        "postalCodes",
        # "applied" embedded in a noun-y entity name shouldn't escalate
        # without a clear verb structure.
        "appliedFilterMetadata",
    ],
)
def test_infer_caution_does_not_escalate_for_lookalike_words(name: str) -> None:
    assert _infer_caution(name) == "low"


def test_caution_explicit_overrides_inferred() -> None:
    """Importers may set caution explicitly; the heuristic shouldn't overwrite."""
    # Even if the name would heuristically be "high", an explicit "medium"
    # in the source should be preserved through schema validation.
    meta = EndpointMetadata(entity_set_name="salesInvoicePost", caution="medium")
    assert meta.caution == "medium"
