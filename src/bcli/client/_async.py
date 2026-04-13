"""Async Business Central client."""

from __future__ import annotations

from typing import Any

from bcli._url import build_companies_url, build_url
from bcli.auth._credentials import ClientCredentialsAuth
from bcli.client._safety import SafeContext
from bcli.client._transport import BCTransport
from bcli.config import BCConfig, BCProfile, load_config
from bcli.errors import ConfigError
from bcli.odata._pagination import PageIterator
from bcli.odata._query import Query
from bcli.odata._response import ODataResponse
from bcli.registry._registry import EndpointRegistry


class AsyncBCClient:
    """Async client for Business Central APIs.

    Two construction modes:

    1. Profile-based (reads TOML config):
        async with AsyncBCClient(profile="production") as client:
            ...

    2. Programmatic (no config files needed):
        async with AsyncBCClient(
            tenant_id="...", client_id="...", client_secret="...",
            environment="Production", company_id="...",
        ) as client:
            ...
    """

    def __init__(
        self,
        *,
        # Profile-based construction
        profile: str | None = None,
        config: BCConfig | None = None,
        # Programmatic construction (no config file needed)
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        environment: str | None = None,
        company_id: str | None = None,
        # Shared options
        timeout: int | None = None,
    ) -> None:
        if tenant_id is not None:
            # Programmatic mode — build a synthetic profile
            self._config = BCConfig()
            self._profile = BCProfile(
                tenant_id=tenant_id,
                environment=environment or "Production",
                company_id=company_id,
                client_id=client_id,
            )
            self._programmatic_secret = client_secret
            self._registry = EndpointRegistry()
        else:
            # Profile-based mode — read from config
            self._config = config or load_config()
            self._profile = self._config.get_profile(profile)
            self._programmatic_secret = None
            self._registry = EndpointRegistry(
                profile_name=profile or self._config.defaults.profile,
                disable_standard=self._profile.disable_standard_api,
                allowed_categories=self._profile.allowed_categories or None,
                allowed_endpoints=self._profile.allowed_endpoints or None,
            )

        self._transport: BCTransport | None = None
        self._timeout = timeout or self._config.defaults.timeout

    def _ensure_transport(self) -> BCTransport:
        if self._transport is None:
            auth = self._build_auth(self._profile, self._programmatic_secret, self._config)
            self._transport = BCTransport(auth, timeout=self._timeout)
        return self._transport

    @staticmethod
    def _build_auth(
        profile: BCProfile,
        programmatic_secret: str | None = None,
        config: BCConfig | None = None,
    ):
        """Build auth provider from profile config or programmatic credentials."""
        if profile.auth_method == "workos":
            from bcli.auth._workos import WorkOSAuth

            workos_cfg = config.workos if config else None
            if not workos_cfg or not workos_cfg.api_key:
                from bcli.errors import ConfigError
                raise ConfigError(
                    "WorkOS auth requires [workos] section in config.toml with api_key and client_id."
                )
            role_mapping = workos_cfg.get_role_mapping()
            # Default BC client_id comes from the profile
            return WorkOSAuth(
                tenant_id=profile.tenant_id,
                workos_api_key=workos_cfg.api_key,
                workos_client_id=workos_cfg.client_id,
                role_mapping=role_mapping,
                default_bc_client_id=profile.client_id or "",
            )

        if profile.auth_method == "browser":
            from bcli.auth._browser import BrowserAuth

            return BrowserAuth(
                tenant_id=profile.tenant_id,
                client_id=profile.client_id or "",
            )

        if profile.auth_method == "device_code":
            from bcli.auth._device_code import DeviceCodeAuth

            return DeviceCodeAuth(
                tenant_id=profile.tenant_id,
                client_id=profile.client_id or "",
            )

        # Client credentials — programmatic secret takes priority over env var
        return ClientCredentialsAuth(
            tenant_id=profile.tenant_id,
            client_id=profile.client_id or "",
            client_secret_env=profile.client_secret_env,
            client_secret=programmatic_secret,
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
        from bcli._url import build_environments_url

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

    def safe_write(
        self,
        environment: str,
        company_id: str,
        *,
        confirm_production: bool = False,
        domain_rules: dict | None = None,
    ) -> SafeContext:
        """Create a SafeContext for gated write operations.

        Usage:
            async with client.safe_write("Sandbox", "company-id") as sw:
                await sw.post("salesInvoices", body={...}, domain="finance")
        """
        return SafeContext(
            client=self,
            environment=environment,
            company_id=company_id,
            confirm_production=confirm_production,
            domain_rules=domain_rules,
        )

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
                "No company_id configured. Run 'bcli config init' or 'bcli company use <id>'."
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
