"""Pagination support for OData nextLink following."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from bcapi.client._transport import BCTransport


class PageIterator:
    """Async iterator that follows @odata.nextLink for full pagination."""

    def __init__(self, transport: BCTransport, first_url: str, params: dict[str, str]) -> None:
        self._transport = transport
        self._next_url: str | None = first_url
        self._params: dict[str, str] | None = params
        self._started = False

    def __aiter__(self) -> AsyncIterator[list[dict[str, Any]]]:
        return self

    async def __anext__(self) -> list[dict[str, Any]]:
        if self._next_url is None and self._started:
            raise StopAsyncIteration

        self._started = True

        if self._params is not None:
            data = await self._transport.get(self._next_url, params=self._params)
            self._params = None  # Only use params on the first request
        else:
            # nextLink URLs are absolute and include their own query params
            data = await self._transport.get_absolute(self._next_url)

        self._next_url = data.get("@odata.nextLink")
        records = data.get("value", [])

        if not records and self._next_url is None:
            raise StopAsyncIteration

        return records
