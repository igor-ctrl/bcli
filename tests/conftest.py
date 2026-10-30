"""Shared test fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _isolate_msal_cache(tmp_path, monkeypatch):
    """Keep the persistent MSAL cache out of the developer's real config dir.

    ``MsalTokenCache`` defaults to ``~/.config/bcli/msal_cache.json``, which
    holds a real refresh token on any machine where bcli has been used. A test
    that constructs ``BrowserAuth`` / ``DeviceCodeAuth`` without an explicit
    ``msal_cache`` would otherwise read — and on save, overwrite — it.

    Autouse so no future test has to remember. Pointing the module-level
    default at ``tmp_path`` is enough; ``MsalTokenCache`` resolves it lazily
    per instance.
    """
    import bcli.auth._msal_cache as msal_cache_mod

    monkeypatch.setattr(
        msal_cache_mod, "MSAL_CACHE_FILE", tmp_path / "msal_cache.json"
    )
    yield


# ─── Fake BC transport ───────────────────────────────────────────────────


@dataclass
class Sent:
    """One request the code under test sent to BC."""

    method: str
    url: str
    json_body: Any = None
    params: Any = None
    etag: str | None = None
    idempotency_key: str | None = None


class FakeTransport:
    """Stands in for ``BCTransport``: records every request, sends nothing.

    Replies come from ``replies`` in order (a dict body, or an exception to
    raise), then ``default``. Tests drive the real ``AsyncBCClient`` — planning,
    URL resolution, audit — and assert on what was sent.
    """

    def __init__(self) -> None:
        self.sent: list[Sent] = []
        self.replies: list[Any] = []
        self.default: dict[str, Any] = {}
        self.status = 200
        self.correlation_id: str | None = "corr-test"

    def reply(self, *bodies: Any) -> FakeTransport:
        self.replies.extend(bodies)
        return self

    @property
    def last(self) -> Sent:
        return self.sent[-1]

    async def request(self, method, url, *, params=None, json_body=None, content=None,
                      content_type=None, etag=None, idempotency_key=None, log_context=None):
        from bcli.client._transport import BCResponse

        self.sent.append(Sent(method, url, json_body, params, etag, idempotency_key))
        nxt = self.replies.pop(0) if self.replies else self.default
        if isinstance(nxt, BaseException):
            raise nxt
        return BCResponse(nxt, self.status, self.correlation_id)

    async def _request(self, method, url, **kwargs):
        return (await self.request(method, url, **kwargs)).body

    async def get(self, url, *, params=None):
        return await self._request("GET", url, params=params)

    async def get_absolute(self, url):
        return await self._request("GET", url)

    async def post(self, url, *, json_body, idempotency_key=None):
        return await self._request("POST", url, json_body=json_body, idempotency_key=idempotency_key)

    async def patch(self, url, *, json_body, etag="*", idempotency_key=None):
        return await self._request("PATCH", url, json_body=json_body, etag=etag,
                                   idempotency_key=idempotency_key)

    async def patch_binary(self, url, *, content, content_type="application/octet-stream",
                           etag="*", idempotency_key=None):
        return await self._request("PATCH", url, content=content, etag=etag,
                                   idempotency_key=idempotency_key)

    async def delete(self, url, *, etag="*", idempotency_key=None):
        return await self._request("DELETE", url, etag=etag, idempotency_key=idempotency_key)

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_bc(monkeypatch) -> FakeTransport:
    """Every ``AsyncBCClient`` in the test talks to one ``FakeTransport``."""
    from bcli.client._async import AsyncBCClient

    transport = FakeTransport()
    monkeypatch.setattr(AsyncBCClient, "_ensure_transport", lambda self: transport)
    return transport
