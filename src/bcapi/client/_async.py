"""Async Business Central client."""

from __future__ import annotations

import os
from typing import Any

from bcapi._url import build_companies_url, build_url
from bcapi.auth._credentials import ClientCredentialsAuth
from bcapi.client._transport import BCTransport
from bcapi.config import BCConfig, BCProfile, load_config
from bcapi.errors import ConfigError
from bcapi.odata._pagination import PageIterator
from bcapi.odata._query import Query
from bcapi.odata._response import ODataResponse
from bcapi.registry._registry import EndpointRegistry


class AsyncBCClient:
    """Async client for Business Central APIs.

    Usage:
        async with AsyncBCClient(profile="production") as client:
            response = await client.query("customers").top(5).execute()
            for record in response:
                print(record)
    """

    def __init__(
        self,
        *,
        profile: str | None = None,
        config: BCConfig | None = None,
        timeout: int | None = None,
    ) -> None:
        self._config = config or load_config()
        self._profile = self._config.get_profile(profile)
        self._registry = EndpointRegistry(profile_name=profile or self._config.defaults.profile)
        self._transport: BCTransport | None = None
        self._timeout = timeout or self._config.defaults.timeout

    def _ensure_transport(self) -> BCTransport:
        if self._transport is None:
            auth = self._build_auth(self._profile)
            self._transport = BCTransport(auth, timeout=self._timeout)
        return self._transport

    @staticmethod
    def _build_auth(profile: BCProfile):
        """Build auth provider from profile config."""
        if profile.auth_method == "device_code":
            from bcapi.auth._device_code import DeviceCodeAuth

            return DeviceCodeAuth(
                tenant_id=profile.tenant_id,
                client_id=profile.client_id or "",
            )

        # Default: client_credentials — secret resolved lazily
        return ClientCredentialsAuth(
            tenant_id=profile.tenant_id,
            client_id=profile.client_id or "",
            client_secret_env=profile.client_secret_env,
        )

    async def __aenter__(self) -> AsyncBCClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._transport:
            await self._transport.close()
            self._transport = None

    # ─── Query API ───────────────────────────────────────────────

    def query(self, entity_set_name: str) -> BoundQuery:
        """Start building a query against an entity."""
        return BoundQuery(self, entity_set_name)

    async def get(
        self,
        entity_set_name: str,
        record_id: str | None = None,
        *,
        query: Query | None = None,
        publisher: str | None = None,
        group: str | None = None,
        version: str | None = None,
    ) -> ODataResponse:
        """Execute a GET request."""
        transport = self._ensure_transport()

        url = self._resolve_url(
            entity_set_name,
            record_id=record_id,
            publisher=publisher,
            group=group,
            version=version,
        )

        params = query.to_params() if query else {}
        data = await transport.get(url, params=params)
        return ODataResponse(data)

    async def post(
        self,
        entity_set_name: str,
        body: dict[str, Any],
        *,
        publisher: str | None = None,
        group: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        """POST (create) a record."""
        transport = self._ensure_transport()
        url = self._resolve_url(entity_set_name, publisher=publisher, group=group, version=version)
        return await transport.post(url, json_body=body)

    async def patch(
        self,
        entity_set_name: str,
        record_id: str,
        body: dict[str, Any],
        *,
        etag: str = "*",
        publisher: str | None = None,
        group: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        """PATCH (update) a record."""
        transport = self._ensure_transport()
        url = self._resolve_url(
            entity_set_name, record_id=record_id,
            publisher=publisher, group=group, version=version,
        )
        return await transport.patch(url, json_body=body, etag=etag)

    async def delete(
        self,
        entity_set_name: str,
        record_id: str,
        *,
        etag: str = "*",
        publisher: str | None = None,
        group: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        """DELETE a record."""
        transport = self._ensure_transport()
        url = self._resolve_url(
            entity_set_name, record_id=record_id,
            publisher=publisher, group=group, version=version,
        )
        return await transport.delete(url, etag=etag)

    async def list_companies(self) -> list[dict[str, Any]]:
        """Discover all companies in the current environment."""
        transport = self._ensure_transport()
        url = build_companies_url(environment=self._profile.environment)
        data = await transport.get(url)
        return data.get("value", [])

    async def list_environments(self) -> list[dict[str, Any]]:
        """Discover all environments via BC Admin Center API."""
        from bcapi._url import build_environments_url

        transport = self._ensure_transport()
        url = build_environments_url(tenant_id=self._profile.tenant_id)
        data = await transport.get(url)
        return data.get("value", [])

    async def test_connection(self) -> bool:
        """Test auth and API reachability."""
        try:
            companies = await self.list_companies()
            return len(companies) > 0
        except Exception:
            return False

    # ─── Internal ────────────────────────────────────────────────

    def _resolve_url(
        self,
        entity_set_name: str,
        *,
        record_id: str | None = None,
        publisher: str | None = None,
        group: str | None = None,
        version: str | None = None,
    ) -> str:
        """Resolve entity to full URL using registry or explicit overrides."""
        if not self._profile.company_id:
            raise ConfigError(
                "No company_id configured. Run 'bcapi config init' or 'bcapi company use <id>'."
            )

        # Explicit override takes priority
        if publisher and group and version:
            return build_url(
                environment=self._profile.environment,
                company_id=self._profile.company_id,
                entity_set_name=entity_set_name,
                record_id=record_id,
                publisher=publisher,
                group=group,
                version=version,
            )

        # Look up in registry
        endpoint = self._registry.get(entity_set_name)

        if endpoint and endpoint.is_custom:
            return build_url(
                environment=self._profile.environment,
                company_id=self._profile.company_id,
                entity_set_name=entity_set_name,
                record_id=record_id,
                publisher=endpoint.api_publisher,
                group=endpoint.api_group,
                version=endpoint.api_version,
            )

        # Standard v2.0 or unknown (try standard route)
        return build_url(
            environment=self._profile.environment,
            company_id=self._profile.company_id,
            entity_set_name=entity_set_name,
            record_id=record_id,
        )

    @property
    def registry(self) -> EndpointRegistry:
        return self._registry

    @property
    def profile(self) -> BCProfile:
        return self._profile


class BoundQuery:
    """A query bound to a specific client and entity, supporting fluent chaining."""

    def __init__(self, client: AsyncBCClient, entity_set_name: str) -> None:
        self._client = client
        self._entity = entity_set_name
        self._query = Query()
        self._publisher: str | None = None
        self._group: str | None = None
        self._version: str | None = None

    def filter(self, expression: str) -> BoundQuery:
        self._query.filter(expression)
        return self

    def select(self, *fields: str) -> BoundQuery:
        self._query.select(*fields)
        return self

    def expand(self, *navigations: str) -> BoundQuery:
        self._query.expand(*navigations)
        return self

    def orderby(self, expression: str) -> BoundQuery:
        self._query.orderby(expression)
        return self

    def top(self, n: int) -> BoundQuery:
        self._query.top(n)
        return self

    def skip(self, n: int) -> BoundQuery:
        self._query.skip(n)
        return self

    def count(self, enabled: bool = True) -> BoundQuery:
        self._query.count(enabled)
        return self

    def route(self, publisher: str, group: str, version: str) -> BoundQuery:
        """Override the API route for this query."""
        self._publisher = publisher
        self._group = group
        self._version = version
        return self

    async def execute(self) -> ODataResponse:
        """Execute the query and return the response."""
        return await self._client.get(
            self._entity,
            query=self._query,
            publisher=self._publisher,
            group=self._group,
            version=self._version,
        )

    async def get(self) -> list[dict]:
        """Execute and return just the records."""
        response = await self.execute()
        return response.value

    async def pages(self) -> PageIterator:
        """Return a page iterator for streaming large result sets."""
        url = self._client._resolve_url(
            self._entity,
            publisher=self._publisher,
            group=self._group,
            version=self._version,
        )
        transport = self._client._ensure_transport()
        return PageIterator(transport, url, self._query.to_params())
