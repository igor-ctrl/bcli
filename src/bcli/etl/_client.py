"""BC reads for the dlt source, on bcli's own transport.

Retry/backoff, BC error mapping, URL validation and the off-origin
``@odata.nextLink`` guard all come from :class:`bcli.client._transport.BCTransport`
and :func:`bcli._url.build_url` — the ETL layer only adapts its token-based
:class:`~bcli.etl._auth.AuthProvider` and pages through entities.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from bcli._url import build_companies_url, build_url
from bcli.client._transport import BCTransport
from bcli.errors import NotFoundError
from bcli.odata._pagination import PageIterator

__all__ = ["BCClient", "NotFoundError", "build_entity_url"]


def build_entity_url(
    *,
    environment: str,
    company_id: str,
    entity_set_name: str,
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
) -> str:
    """Build the full BC URL for an entity (validated like every other bcli URL)."""
    return build_url(
        environment=environment,
        company_id=company_id,
        entity_set_name=entity_set_name,
        publisher=publisher,
        group=group,
        version=version,
    )


class _TokenAuth:
    """Adapts an ETL ``get_token()`` provider to bcli's ``get_access_token()``."""

    def __init__(self, auth: Any) -> None:
        self._auth = auth

    async def get_access_token(self) -> str:
        return await self._auth.get_token()


class BCClient:
    """Async BC reader for the dlt source.

    ``auth`` is either an ETL provider (``get_token``) or a bcli
    :class:`~bcli.auth.AuthProvider` (``get_access_token``).

    Example:
        >>> async with BCClient(auth=my_auth, environment="Production") as client:
        ...     async for page in client.paginate(url, params={"$filter": "..."}):
        ...         process(page)
    """

    def __init__(self, *, auth: Any, environment: str, timeout: float = 60.0) -> None:
        if not hasattr(auth, "get_access_token"):
            auth = _TokenAuth(auth)
        self._auth = auth
        self._environment = environment
        self._timeout = timeout
        self._transport: BCTransport | None = None

    @property
    def environment(self) -> str:
        return self._environment

    async def __aenter__(self) -> "BCClient":
        self._transport = BCTransport(self._auth, timeout=int(self._timeout))
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._transport is not None:
            await self._transport.close()
            self._transport = None

    def _t(self) -> BCTransport:
        assert self._transport is not None, "BCClient must be used as a context manager"
        return self._transport

    async def get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Single GET."""
        return await self._t().get(url, params=params)

    async def paginate(
        self, url: str, params: dict[str, str] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Follow ``@odata.nextLink`` until exhausted. Yields one page at a time."""
        async for page in PageIterator(self._t(), url, params or {}):
            yield page

    async def list_companies(self) -> list[dict[str, Any]]:
        """Return all companies in the environment."""
        data = await self.get(build_companies_url(environment=self._environment))
        return data.get("value", [])
