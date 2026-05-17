"""Transport sends the Idempotency-Key header when set on the client method.

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

import httpx
import pytest

from bcli.client._async import AsyncBCClient


class _FakeTransport:
    """Tiny stub that records request kwargs for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def request(self, method, url, **kwargs):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(kwargs.get("headers", {}))}
        )
        # Return a minimal valid OData response.
        return httpx.Response(
            201, json={"systemId": "rec-1", "id": "rec-1"}, request=httpx.Request(method, url),
        )


@pytest.fixture
def fake_client(monkeypatch):
    """Build an AsyncBCClient wired to a fake httpx layer."""
    client = AsyncBCClient.__new__(AsyncBCClient)
    # The minimal attributes the post/patch/delete paths touch.
    client._closed = False  # type: ignore[attr-defined]
    transport_stub = _FakeTransport()

    # Patch _ensure_transport to return our stub for the post path.
    class _StubTransport:
        def __init__(self):
            self._client = transport_stub
            self.calls = transport_stub.calls

        async def post(self, url, *, json_body, idempotency_key=None):
            headers = {}
            if idempotency_key is not None:
                headers["Idempotency-Key"] = idempotency_key
            transport_stub.calls.append(
                {"method": "POST", "url": url, "headers": headers, "json": json_body}
            )
            return {"systemId": "rec-1", "id": "rec-1"}

        async def patch(self, url, *, json_body, etag="*", idempotency_key=None):
            headers = {"If-Match": etag}
            if idempotency_key is not None:
                headers["Idempotency-Key"] = idempotency_key
            transport_stub.calls.append(
                {"method": "PATCH", "url": url, "headers": headers, "json": json_body}
            )
            return {"systemId": "rec-1"}

        async def delete(self, url, *, etag="*", idempotency_key=None):
            headers = {"If-Match": etag}
            if idempotency_key is not None:
                headers["Idempotency-Key"] = idempotency_key
            transport_stub.calls.append(
                {"method": "DELETE", "url": url, "headers": headers}
            )
            return {}

    stub = _StubTransport()
    monkeypatch.setattr(client, "_ensure_transport", lambda: stub, raising=False)
    monkeypatch.setattr(client, "_resolve_url", lambda *a, **kw: "https://example.com/api/x", raising=False)
    return client, stub


def test_post_passes_idempotency_key_to_transport(fake_client):
    client, stub = fake_client
    asyncio.run(
        client.post("vendors", {"name": "Acme"}, idempotency_key="op-abc")
    )
    assert stub.calls[-1]["method"] == "POST"
    assert stub.calls[-1]["headers"].get("Idempotency-Key") == "op-abc"


def test_post_without_idempotency_key_omits_header(fake_client):
    client, stub = fake_client
    asyncio.run(client.post("vendors", {"name": "Acme"}))
    assert "Idempotency-Key" not in stub.calls[-1]["headers"]


def test_patch_passes_idempotency_key_to_transport(fake_client):
    client, stub = fake_client
    asyncio.run(
        client.patch("vendors", "rec-1", {"name": "Renamed"},
                     idempotency_key="op-patch")
    )
    assert stub.calls[-1]["headers"].get("Idempotency-Key") == "op-patch"


def test_delete_passes_idempotency_key_to_transport(fake_client):
    client, stub = fake_client
    asyncio.run(
        client.delete("vendors", "rec-1", idempotency_key="op-del")
    )
    assert stub.calls[-1]["headers"].get("Idempotency-Key") == "op-del"
