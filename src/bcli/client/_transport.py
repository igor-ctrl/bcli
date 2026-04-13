"""HTTP transport layer built on httpx with retry, rate limiting, and BC error parsing."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bcli.auth._base import AuthProvider
from bcli.errors import (
    AuthError,
    BCLIError,
    ForbiddenError,
    NotFoundError,
    ServerError,
    ThrottledError,
    ValidationError,
)

logger = logging.getLogger(__name__)

# Status code → exception class mapping
_ERROR_MAP: dict[int, type[BCLIError]] = {
    400: ValidationError,
    401: AuthError,
    403: ForbiddenError,
    404: NotFoundError,
    429: ThrottledError,
    500: ServerError,
    502: ServerError,
    503: ServerError,
    504: ServerError,
}

# Retryable status codes
_RETRYABLE = {429, 503, 504}

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds


class BCTransport:
    """HTTP transport with auth injection, retry, and BC error parsing."""

    def __init__(
        self,
        auth: AuthProvider,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._auth = auth
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "OData-Version": "4.0",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _inject_auth(self) -> dict[str, str]:
        token = await self._auth.get_access_token()
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        etag: str | None = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request with retry and error handling."""
        last_error: Exception | None = None
        backoff = INITIAL_BACKOFF

        for attempt in range(self._max_retries + 1):
            try:
                auth_headers = await self._inject_auth()
                headers = dict(auth_headers)
                if etag:
                    headers["If-Match"] = etag

                logger.debug("%s %s (attempt %d)", method, url, attempt + 1)

                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )

                if response.is_success:
                    if response.status_code == 204 or not response.content:
                        return {}
                    return response.json()

                # Parse BC error response
                bc_message, correlation_id = _parse_bc_error(response)
                status = response.status_code

                # Retry on retryable errors
                if status in _RETRYABLE and attempt < self._max_retries:
                    retry_after = _get_retry_after(response)
                    wait = retry_after if retry_after else backoff
                    logger.warning(
                        "Retryable error %d on %s, waiting %.1fs (attempt %d/%d)",
                        status, url, wait, attempt + 1, self._max_retries + 1,
                    )
                    import asyncio
                    await asyncio.sleep(wait)
                    backoff *= 2
                    continue

                # Raise appropriate error
                error_cls = _ERROR_MAP.get(status, BCLIError)
                kwargs: dict[str, Any] = {
                    "status_code": status,
                    "bc_message": bc_message,
                    "correlation_id": correlation_id,
                }
                if status == 429:
                    kwargs["retry_after"] = _get_retry_after(response)

                raise error_cls(
                    f"HTTP {status} {response.reason_phrase}: {method} {url}",
                    **kwargs,
                )

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
                last_error = e
                if attempt < self._max_retries:
                    logger.warning(
                        "Network error on %s %s: %s, retrying in %.1fs",
                        method, url, e, backoff,
                    )
                    import asyncio
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise ServerError(
                    f"Network error after {self._max_retries + 1} attempts: {e}",
                ) from e

        raise ServerError(f"Request failed after {self._max_retries + 1} attempts") from last_error

    # Convenience methods

    async def get(self, url: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        return await self._request("GET", url, params=params)

    async def get_absolute(self, url: str) -> dict[str, Any]:
        """GET with an absolute URL (e.g., nextLink)."""
        return await self._request("GET", url)

    async def post(self, url: str, *, json_body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", url, json_body=json_body)

    async def patch(
        self, url: str, *, json_body: dict[str, Any], etag: str = "*"
    ) -> dict[str, Any]:
        return await self._request("PATCH", url, json_body=json_body, etag=etag)

    async def delete(self, url: str, *, etag: str = "*") -> dict[str, Any]:
        return await self._request("DELETE", url, etag=etag)


def _parse_bc_error(response: httpx.Response) -> tuple[str | None, str | None]:
    """Extract error message and correlation ID from a BC error response."""
    bc_message = None
    correlation_id = response.headers.get("x-ms-correlation-request-id")

    try:
        body = response.json()
        error = body.get("error", {})
        bc_message = error.get("message", "")
    except Exception:
        pass

    return bc_message, correlation_id


def _get_retry_after(response: httpx.Response) -> float | None:
    """Parse Retry-After header."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
