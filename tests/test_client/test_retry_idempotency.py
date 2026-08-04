"""Automatic retry must not silently re-run a non-idempotent request.

The transport retried 429/503/504 and network errors for *every* method. For a
GET that is free. For a POST, PATCH or DELETE it is not: the server may already
have applied the request when the response was lost, so a retry duplicates a
create or re-runs a business action.

That is not theoretical for this SDK. Bound actions like the LLP-utilisation
recalculation take no arguments and mutate on every invocation — there is no
such thing as a harmless repeat. A single 503 from a gateway could recalculate
twice, and nothing in the log would say so.

So retries are now allowed only when repeating the request is safe:

* the method is read-only (GET / HEAD), or
* the caller supplied an ``Idempotency-Key``, which is what lets a gateway (or a
  future server-side implementation) collapse the duplicate.

Otherwise the error surfaces. A visible transient failure the caller can retry
deliberately is strictly better than an invisible double-write.
"""

from __future__ import annotations

import httpx
import pytest

from bcli.client._transport import BCTransport
from bcli.errors import ServerError


class _StubAuth:
    async def get_access_token(self) -> str:
        return "token"

    def clear_cache(self) -> None:
        return None


def _transport(handler: httpx.MockTransport) -> BCTransport:
    t = BCTransport(_StubAuth(), max_retries=2)
    t._client = httpx.AsyncClient(transport=handler)
    return t


def _counting_handler(status: int):
    """Always answers `status`; records how many times it was called."""
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(status, json={"error": {"message": "nope"}})

    return httpx.MockTransport(handle), calls


class TestReadsStillRetry:
    @pytest.mark.parametrize("status", [429, 503, 504])
    async def test_get_retries_as_before(self, status):
        handler, calls = _counting_handler(status)
        t = _transport(handler)
        with pytest.raises(Exception):
            await t._request("GET", "https://example.test/x")
        assert len(calls) == 3, "GET should still use all attempts"


class TestMutationsDoNotRetryWithoutAKey:
    @pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
    @pytest.mark.parametrize("status", [429, 503, 504])
    async def test_single_attempt_only(self, method, status):
        handler, calls = _counting_handler(status)
        t = _transport(handler)
        with pytest.raises(Exception):
            await t._request(method, "https://example.test/x")
        assert len(calls) == 1, (
            f"{method} was retried without an idempotency key; a lost response "
            f"would duplicate the write"
        )

    @pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
    async def test_network_error_is_not_retried_either(self, method):
        """The dangerous case: the request may have been applied before the
        connection dropped, so there is no way to know a retry is safe."""
        calls: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            raise httpx.ReadTimeout("boom", request=request)

        t = _transport(httpx.MockTransport(handle))
        with pytest.raises(ServerError):
            await t._request(method, "https://example.test/x")
        assert len(calls) == 1


class TestAnIdempotencyKeyReEnablesRetry:
    @pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
    async def test_key_allows_retry(self, method):
        handler, calls = _counting_handler(503)
        t = _transport(handler)
        with pytest.raises(Exception):
            await t._request(method, "https://example.test/x", idempotency_key="k-1")
        assert len(calls) == 3

    async def test_the_key_is_actually_sent(self):
        seen: list[str | None] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Idempotency-Key"))
            return httpx.Response(200, json={})

        t = _transport(httpx.MockTransport(handle))
        await t._request("POST", "https://example.test/x", idempotency_key="k-2")
        assert seen == ["k-2"]


class TestSuccessPathUnaffected:
    @pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
    async def test_a_successful_mutation_still_works(self, method):
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        t = _transport(httpx.MockTransport(handle))
        assert await t._request(method, "https://example.test/x") == {"ok": True}
