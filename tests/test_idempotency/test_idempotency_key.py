"""The client hands the idempotency key to the transport on every write verb.

This is the http-layer half of AIP §Phase 4d. The CLI half — flag plumbing,
ledger persistence, same-run replay protection — lives in
``tests/test_batch_ledger/test_ledger_idempotency.py`` and the batch run
integration tests.

The BC Online API doesn't currently document Idempotency-Key as a
first-class feature, but standardizing on the IETF draft header
(``Idempotency-Key: <value>``) means any reverse proxy / gateway in
front of BC can apply replay protection too, and lets us ledger the
key for our own retry logic regardless of server support.
"""

from __future__ import annotations

import asyncio

import pytest

from bcli.client._async import AsyncBCClient


@pytest.fixture
def client(fake_bc):
    """A real programmatic client whose transport is ``fake_bc``."""
    fake_bc.default = {"systemId": "rec-1", "id": "rec-1"}
    return AsyncBCClient(tenant_id="t1", environment="Sandbox", company_id="c-123")


def test_post_passes_idempotency_key_to_transport(client, fake_bc):
    asyncio.run(
        client.post("vendors", {"name": "Acme"}, idempotency_key="op-abc")
    )
    assert fake_bc.last.method == "POST"
    assert fake_bc.last.url.endswith("/companies(c-123)/vendors")
    assert fake_bc.last.idempotency_key == "op-abc"


def test_post_without_idempotency_key_omits_header(client, fake_bc):
    asyncio.run(client.post("vendors", {"name": "Acme"}))
    assert fake_bc.last.idempotency_key is None


def test_patch_passes_idempotency_key_to_transport(client, fake_bc):
    asyncio.run(
        client.patch("vendors", "rec-1", {"name": "Renamed"},
                     idempotency_key="op-patch")
    )
    assert fake_bc.last.method == "PATCH"
    assert fake_bc.last.url.endswith("/vendors(rec-1)")
    assert fake_bc.last.idempotency_key == "op-patch"


def test_delete_passes_idempotency_key_to_transport(client, fake_bc):
    asyncio.run(
        client.delete("vendors", "rec-1", idempotency_key="op-del")
    )
    assert fake_bc.last.method == "DELETE"
    assert fake_bc.last.idempotency_key == "op-del"
