"""Tests for BCTransport — retry, auth injection, error parsing, structured logging."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from bcli.client._transport import (
    BCTransport,
    _get_retry_after,
    _parse_bc_error,
)
from bcli.errors import (
    AuthError,
    NotFoundError,
    ServerError,
    ThrottledError,
    ValidationError,
)


class FakeAuth:
    """Fake auth provider that returns a fixed token."""

    def __init__(self, token: str = "fake-token") -> None:
        self._token = token

    async def get_access_token(self) -> str:
        return self._token


def _make_response(
    status_code: int,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    """Build a fake httpx.Response."""
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    resp = httpx.Response(
        status_code=status_code,
        headers=hdrs,
        content=content or (json.dumps(json_body).encode() if json_body else b""),
        request=httpx.Request("GET", "https://example.com"),
    )
    return resp


# ── _parse_bc_error ───────────────────────────────────────────────────────

class TestParseBCError:
    def test_extracts_message_and_correlation_id(self):
        resp = _make_response(
            400,
            json_body={"error": {"message": "Bad filter syntax"}},
            headers={"x-ms-correlation-request-id": "abc-123"},
        )
        msg, cid = _parse_bc_error(resp)
        assert msg == "Bad filter syntax"
        assert cid == "abc-123"

    def test_empty_body(self):
        resp = _make_response(500, content=b"")
        msg, cid = _parse_bc_error(resp)
        assert msg is None
        assert cid is None

    def test_non_json_body(self):
        resp = _make_response(502, content=b"<html>Bad Gateway</html>")
        msg, cid = _parse_bc_error(resp)
        assert msg is None

    def test_missing_error_key(self):
        resp = _make_response(400, json_body={"something": "else"})
        msg, cid = _parse_bc_error(resp)
        assert msg == ""


# ── _get_retry_after ──────────────────────────────────────────────────────

class TestGetRetryAfter:
    def test_valid_float(self):
        resp = _make_response(429, headers={"Retry-After": "2.5"})
        assert _get_retry_after(resp) == 2.5

    def test_valid_int(self):
        resp = _make_response(429, headers={"Retry-After": "5"})
        assert _get_retry_after(resp) == 5.0

    def test_missing(self):
        resp = _make_response(429)
        assert _get_retry_after(resp) is None

    def test_invalid(self):
        resp = _make_response(429, headers={"Retry-After": "not-a-number"})
        assert _get_retry_after(resp) is None


# ── BCTransport._request ─────────────────────────────────────────────────

class TestTransportRequest:
    @pytest.fixture
    def transport(self):
        t = BCTransport(FakeAuth(), timeout=5, max_retries=2)
        yield t

    async def test_success_injects_auth_header(self, transport, httpx_mock):
        httpx_mock.add_response(
            json={"value": [{"id": "1"}]},
            headers={"x-ms-correlation-request-id": "corr-1"},
        )
        result = await transport.get("https://api.example.com/customers")
        assert result == {"value": [{"id": "1"}]}

        req = httpx_mock.get_request()
        assert req.headers["authorization"] == "Bearer fake-token"

    async def test_204_returns_empty_dict(self, transport, httpx_mock):
        httpx_mock.add_response(status_code=204)
        result = await transport.get("https://api.example.com/something")
        assert result == {}

    async def test_400_raises_validation_error(self, transport, httpx_mock):
        httpx_mock.add_response(
            status_code=400,
            json={"error": {"message": "Invalid filter"}},
        )
        with pytest.raises(ValidationError, match="Invalid filter"):
            await transport.get("https://api.example.com/bad")

    async def test_401_raises_auth_error(self, transport, httpx_mock):
        httpx_mock.add_response(
            status_code=401,
            json={"error": {"message": "Token expired"}},
        )
        with pytest.raises(AuthError):
            await transport.get("https://api.example.com/auth")

    async def test_404_raises_not_found(self, transport, httpx_mock):
        httpx_mock.add_response(
            status_code=404,
            json={"error": {"message": "Not found"}},
        )
        with pytest.raises(NotFoundError):
            await transport.get("https://api.example.com/missing")

    async def test_429_retries_then_raises(self, transport, httpx_mock):
        # 3 responses: 2 retries + 1 final = raises after max_retries
        for _ in range(3):
            httpx_mock.add_response(
                status_code=429,
                json={"error": {"message": "Throttled"}},
                headers={"Retry-After": "0.01"},
            )
        with pytest.raises(ThrottledError):
            await transport.get("https://api.example.com/throttled")

    async def test_429_retry_succeeds(self, transport, httpx_mock):
        # First call: 429, second call: success
        httpx_mock.add_response(
            status_code=429,
            json={"error": {"message": "Throttled"}},
            headers={"Retry-After": "0.01"},
        )
        httpx_mock.add_response(json={"value": []})

        result = await transport.get("https://api.example.com/data")
        assert result == {"value": []}

    async def test_503_retries(self, transport, httpx_mock):
        httpx_mock.add_response(status_code=503, json={"error": {"message": "Unavailable"}})
        httpx_mock.add_response(json={"value": [{"ok": True}]})

        result = await transport.get("https://api.example.com/data")
        assert result["value"][0]["ok"] is True

    async def test_max_retries_exceeded_raises_server_error(self, transport, httpx_mock):
        for _ in range(3):
            httpx_mock.add_response(status_code=504, json={"error": {"message": "Timeout"}})
        with pytest.raises(ServerError):
            await transport.get("https://api.example.com/slow")

    async def test_post_sends_json_body(self, transport, httpx_mock):
        httpx_mock.add_response(json={"id": "new-1", "name": "Test"})
        result = await transport.post(
            "https://api.example.com/items",
            json_body={"name": "Test"},
        )
        assert result["id"] == "new-1"
        req = httpx_mock.get_request()
        assert json.loads(req.content) == {"name": "Test"}

    async def test_patch_sends_etag(self, transport, httpx_mock):
        httpx_mock.add_response(json={"id": "1", "name": "Updated"})
        await transport.patch(
            "https://api.example.com/items/1",
            json_body={"name": "Updated"},
            etag="W/\"etag-123\"",
        )
        req = httpx_mock.get_request()
        assert req.headers["if-match"] == "W/\"etag-123\""

    async def test_delete_returns_empty(self, transport, httpx_mock):
        httpx_mock.add_response(status_code=204)
        result = await transport.delete("https://api.example.com/items/1")
        assert result == {}


# ── Structured logging ────────────────────────────────────────────────────

class TestStructuredLogging:
    async def test_success_logs_json(self, httpx_mock, caplog):
        httpx_mock.add_response(
            json={"value": []},
            headers={"x-ms-correlation-request-id": "log-corr-1"},
        )
        transport = BCTransport(FakeAuth(), timeout=5, max_retries=0)

        with caplog.at_level(logging.INFO, logger="bcli.http"):
            await transport.get("https://api.example.com/test")

        assert len(caplog.records) == 1
        record = json.loads(caplog.records[0].message)
        assert record["event"] == "bc_http_request"
        assert record["method"] == "GET"
        assert record["status"] == 200
        assert record["correlation_id"] == "log-corr-1"
        assert record["retry_count"] == 0
        assert "latency_ms" in record

    async def test_error_logs_with_error_field(self, httpx_mock, caplog):
        httpx_mock.add_response(
            status_code=400,
            json={"error": {"message": "Bad filter"}},
        )
        transport = BCTransport(FakeAuth(), timeout=5, max_retries=0)

        with caplog.at_level(logging.INFO, logger="bcli.http"):
            with pytest.raises(ValidationError):
                await transport.get("https://api.example.com/bad")

        assert len(caplog.records) == 1
        record = json.loads(caplog.records[0].message)
        assert record["error"] == "Bad filter"
        assert record["status"] == 400

    async def test_log_context_passed_through(self, httpx_mock, caplog):
        httpx_mock.add_response(json={"value": []})
        transport = BCTransport(FakeAuth(), timeout=5, max_retries=0)

        with caplog.at_level(logging.INFO, logger="bcli.http"):
            await transport._request(
                "GET", "https://api.example.com/test",
                log_context={"endpoint_tier": "standard", "environment": "Sandbox"},
            )

        record = json.loads(caplog.records[0].message)
        assert record["endpoint_tier"] == "standard"
        assert record["environment"] == "Sandbox"
