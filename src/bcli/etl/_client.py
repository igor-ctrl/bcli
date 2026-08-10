"""Minimal async BC client for the generic ETL layer.

Uses httpx directly with an injectable AuthProvider. Handles OData
pagination (``@odata.nextLink``), 429/503/504 retry with exponential
backoff, and structured URL building.

This module is part of the generic layer and must not import from bcli.*.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from bcli.etl._auth import AuthProvider

_BASE_URL = "https://api.businesscentral.dynamics.com/v2.0"
_STANDARD_API_PATH = "api/v2.0"
_MAX_RETRIES = 3
_INITIAL_BACKOFF = 1.0
_RETRY_STATUSES = (429, 503, 504)

# Allowed host suffixes for absolute URLs that get a BC bearer token attached.
# Mirrors ``bcli._url._ALLOWED_HOST_SUFFIXES`` — kept inline because this
# module is part of the dlt-friendly "generic" layer that must avoid
# reaching into ``bcli.*`` outside of ``bcli.etl.*``. If a malicious BC
# response ever returns an off-origin ``@odata.nextLink``, the paginator
# below would otherwise leak the bearer token to that host.
_ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (
    "businesscentral.dynamics.com",
    "bc.dynamics.com",
)


def _assert_bc_origin(url: str) -> None:
    """Raise ``ValueError`` if ``url`` isn't relative or a BC host."""
    parsed = urlparse(url)
    if not parsed.scheme:
        return  # relative URL, joined to base by httpx
    if parsed.scheme != "https":
        # Bearer tokens never ride cleartext; BC always serves https.
        raise ValueError(f"Refusing non-HTTPS URL with auth: {url!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"Refusing URL with no host: {url!r}")
    for suffix in _ALLOWED_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return
    raise ValueError(
        f"Refusing to attach BC credentials to off-origin URL: {url!r}. "
        f"Allowed host suffixes: {list(_ALLOWED_HOST_SUFFIXES)}."
    )


def build_entity_url(
    *,
    environment: str,
    company_id: str,
    entity_set_name: str,
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
) -> str:
    """Build the full BC URL for an entity."""
    if publisher and group and version:
        api_path = f"api/{publisher}/{group}/{version}"
    else:
        api_path = _STANDARD_API_PATH
    return f"{_BASE_URL}/{environment}/{api_path}/companies({company_id})/{entity_set_name}"


def build_companies_url(environment: str) -> str:
    """Build the URL to list companies in an environment."""
    return f"{_BASE_URL}/{environment}/{_STANDARD_API_PATH}/companies"


class NotFoundError(Exception):
    """Raised when BC returns 404 (entity missing in a company, etc.)."""


class BCClient:
    """Minimal async BC client.

    Example:
        >>> async with BCClient(auth=my_auth, environment="Production") as client:
        ...     async for page in client.paginate(url, params={"$filter": "..."}):
        ...         process(page)
    """

    def __init__(
        self,
        *,
        auth: AuthProvider,
        environment: str,
        timeout: float = 60.0,
    ) -> None:
        self._auth = auth
        self._environment = environment
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def environment(self) -> str:
        return self._environment

    async def __aenter__(self) -> "BCClient":
        self._http = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        assert self._http is not None, "BCClient must be used as a context manager"

        # Validate origin before attaching the bearer token. The paginator
        # below feeds @odata.nextLink straight back through this method, so
        # a malicious BC response cannot use that path to redirect auth.
        _assert_bc_origin(url)

        backoff = _INITIAL_BACKOFF
        for attempt in range(_MAX_RETRIES + 1):
            token = await self._auth.get_token()
            headers = {"Authorization": f"Bearer {token}"}
            response = await self._http.request(
                method, url, params=params, headers=headers
            )

            if response.status_code == 404:
                raise NotFoundError(f"{method} {url} → 404")

            if response.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                retry_after = response.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after else backoff
                await asyncio.sleep(sleep_for)
                backoff *= 2
                continue

            if response.status_code >= 400:
                response.raise_for_status()

            if not response.content:
                return {}
            return response.json()

        raise RuntimeError(f"{method} {url} failed after {_MAX_RETRIES} retries")

    async def get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Single GET."""
        return await self._request("GET", url, params=params)

    async def paginate(
        self, url: str, params: dict[str, str] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Follow ``@odata.nextLink`` until exhausted. Yields one page at a time."""
        next_url: str | None = url
        current_params: dict[str, str] | None = params
        while next_url:
            data = await self._request("GET", next_url, params=current_params)
            yield data.get("value", [])
            next_url = data.get("@odata.nextLink")
            current_params = None  # nextLink URLs are absolute

    async def list_companies(self) -> list[dict[str, Any]]:
        """Return all companies in the environment."""
        data = await self.get(build_companies_url(self._environment))
        return data.get("value", [])
