"""Synchronous wrapper around AsyncBCClient."""

from __future__ import annotations

import asyncio
from typing import Any

from bcapi.client._async import AsyncBCClient
from bcapi.config import BCConfig
from bcapi.odata._query import Query
from bcapi.odata._response import ODataResponse
from bcapi.registry._registry import EndpointRegistry


class BCClient:
    """Synchronous client for Business Central APIs.

    Wraps AsyncBCClient for use in CLI, scripts, and sync contexts.

    Usage:
        client = BCClient(profile="production")
        records = client.query("customers").top(5).get()
    """

    def __init__(
        self,
        *,
        profile: str | None = None,
        config: BCConfig | None = None,
        timeout: int | None = None,
    ) -> None:
        self._async = AsyncBCClient(profile=profile, config=config, timeout=timeout)

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an existing event loop — use a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

    def close(self) -> None:
        self._run(self._async.close())

    def query(self, entity_set_name: str) -> SyncBoundQuery:
        return SyncBoundQuery(self, entity_set_name)

    def get(
        self,
        entity_set_name: str,
        record_id: str | None = None,
        *,
        query: Query | None = None,
        publisher: str | None = None,
        group: str | None = None,
        version: str | None = None,
    ) -> ODataResponse:
        return self._run(
            self._async.get(
                entity_set_name, record_id,
                query=query, publisher=publisher, group=group, version=version,
            )
        )

    def post(self, entity_set_name: str, body: dict[str, Any], **kwargs) -> dict[str, Any]:
        return self._run(self._async.post(entity_set_name, body, **kwargs))

    def patch(
        self, entity_set_name: str, record_id: str, body: dict[str, Any], **kwargs
    ) -> dict[str, Any]:
        return self._run(self._async.patch(entity_set_name, record_id, body, **kwargs))

    def delete(self, entity_set_name: str, record_id: str, **kwargs) -> dict[str, Any]:
        return self._run(self._async.delete(entity_set_name, record_id, **kwargs))

    def list_companies(self) -> list[dict[str, Any]]:
        return self._run(self._async.list_companies())

    def list_environments(self) -> list[dict[str, Any]]:
        return self._run(self._async.list_environments())

    def test_connection(self) -> bool:
        return self._run(self._async.test_connection())

    @property
    def registry(self) -> EndpointRegistry:
        return self._async.registry

    @property
    def profile(self):
        return self._async.profile


class SyncBoundQuery:
    """Sync fluent query builder."""

    def __init__(self, client: BCClient, entity_set_name: str) -> None:
        self._client = client
        self._entity = entity_set_name
        self._query = Query()
        self._publisher: str | None = None
        self._group: str | None = None
        self._version: str | None = None

    def filter(self, expression: str) -> SyncBoundQuery:
        self._query.filter(expression)
        return self

    def select(self, *fields: str) -> SyncBoundQuery:
        self._query.select(*fields)
        return self

    def expand(self, *navigations: str) -> SyncBoundQuery:
        self._query.expand(*navigations)
        return self

    def orderby(self, expression: str) -> SyncBoundQuery:
        self._query.orderby(expression)
        return self

    def top(self, n: int) -> SyncBoundQuery:
        self._query.top(n)
        return self

    def skip(self, n: int) -> SyncBoundQuery:
        self._query.skip(n)
        return self

    def route(self, publisher: str, group: str, version: str) -> SyncBoundQuery:
        self._publisher = publisher
        self._group = group
        self._version = version
        return self

    def get(self) -> list[dict]:
        response = self._client.get(
            self._entity,
            query=self._query,
            publisher=self._publisher,
            group=self._group,
            version=self._version,
        )
        return response.value

    def execute(self) -> ODataResponse:
        return self._client.get(
            self._entity,
            query=self._query,
            publisher=self._publisher,
            group=self._group,
            version=self._version,
        )
