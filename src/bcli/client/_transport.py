"""HTTP transport layer built on httpx with retry, rate limiting, and BC error parsing."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from bcli._url import assert_bc_origin
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
_request_logger = logging.getLogger("bcli.http")

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

#: Methods that can be repeated without applying an effect twice. Deliberately
#: excludes DELETE and PUT: both are idempotent by HTTP semantics, but a repeat
#: here surfaces as a 404 or overwrites a concurrent change, and neither is what
#: an automatic retry should decide on the caller's behalf. Pass an
#: ``idempotency_key`` to opt a mutation back into retrying.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD"})

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds

# BC's "property not found" message. We extract the offending field and the
# entity set from the URL so the hint can name the exact `bcli endpoint
# fields <name>` command to run. This pattern is BC-stable: same wording
# across v2.0 standard and custom v1.x APIs.
_PROPERTY_NOT_FOUND_RE = re.compile(
    r"Could not find a property named '([^']+)' on type 'Microsoft\.NAV\.\w+'",
)
_ENTITY_FROM_URL_RE = re.compile(r"/companies\([^)]+\)/([A-Za-z0-9_]+)")


def _hint_for_bc_error(status: int, bc_message: str | None, url: str) -> str | None:
    """Compute a one-line follow-up command hint for known BC error patterns.

    Returns ``None`` when the error doesn't match a known pattern; the caller
    should leave the message unchanged in that case. The goal is to teach an
    AI agent (or human) the *next bcli command* to run at the moment of
    failure, not to explain the error itself.
    """
    if not bc_message:
        return None

    if status == 400:
        m = _PROPERTY_NOT_FOUND_RE.search(bc_message)
        if m:
            entity_match = _ENTITY_FROM_URL_RE.search(url)
            if entity_match:
                entity = entity_match.group(1)
                return (
                    f"Run 'bcli endpoint fields {entity}' to discover the actual "
                    f"field names on this endpoint. Don't guess them — BC custom "
                    f"APIs don't always follow obvious naming."
                )
            return (
                "Run 'bcli endpoint fields <endpoint>' to discover the actual "
                "field names on this endpoint."
            )

    return None


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
        content: bytes | None = None,
        content_type: str | None = None,
        etag: str | None = None,
        idempotency_key: str | None = None,
        log_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request with retry and error handling.

        Request body handling:
        - ``json_body`` is sent via httpx's ``json=`` (Content-Type: application/json).
        - ``content`` (raw bytes) is sent via httpx's ``content=`` with the
          ``content_type`` header overriding the client default (defaults to
          ``application/octet-stream`` when ``content`` is set but ``content_type``
          isn't). Used for binary uploads (e.g. PATCH /content on attachments).
        - Passing both is an error — the caller should pick one.
        """
        if json_body is not None and content is not None:
            raise ValueError("BCTransport._request: pass either json_body or content, not both")

        last_error: Exception | None = None
        backoff = INITIAL_BACKOFF
        t0 = time.monotonic()

        # Repeating a request is only safe when it cannot apply an effect twice.
        # A read can always be repeated. A POST / PATCH / DELETE cannot: the
        # server may already have applied it when the response was lost, so a
        # retry duplicates a create or re-runs a business action. Some bound
        # actions in this API take no arguments and mutate on every invocation,
        # so there is no such thing as a harmless repeat — one 503 from a gateway
        # could recalculate twice with nothing in the log to say so.
        #
        # An Idempotency-Key re-enables retry, because that is what lets a
        # gateway (or a future server-side implementation) collapse the
        # duplicate. Without one, the error surfaces instead: a visible
        # transient failure the caller can retry deliberately beats an invisible
        # double-write.
        retry_safe = method.upper() in _IDEMPOTENT_METHODS or idempotency_key is not None

        for attempt in range(self._max_retries + 1):
            try:
                auth_headers = await self._inject_auth()
                headers = dict(auth_headers)
                if etag:
                    headers["If-Match"] = etag
                if content is not None:
                    headers["Content-Type"] = content_type or "application/octet-stream"
                if idempotency_key is not None:
                    # AIP §Phase 4d — IETF draft "Idempotency-Key" header.
                    # BC may not honor it server-side today; any gateway /
                    # reverse-proxy in front can apply replay protection,
                    # and we ledger the key so our own retry logic stays
                    # deterministic regardless of server support.
                    headers["Idempotency-Key"] = idempotency_key

                logger.debug("%s %s (attempt %d)", method, url, attempt + 1)

                if content is not None:
                    response = await self._client.request(
                        method,
                        url,
                        params=params,
                        content=content,
                        headers=headers,
                    )
                else:
                    response = await self._client.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        headers=headers,
                    )

                correlation_id = response.headers.get("x-ms-correlation-request-id")

                if response.is_success:
                    self._emit_request_log(
                        method, url, response.status_code, attempt,
                        time.monotonic() - t0, correlation_id, log_context,
                    )
                    if response.status_code == 204 or not response.content:
                        return {}
                    return response.json()

                # Parse BC error response
                bc_message, correlation_id = _parse_bc_error(response)
                status = response.status_code

                # Retry on retryable errors — but only if repeating is safe.
                if status in _RETRYABLE and attempt < self._max_retries and retry_safe:
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

                # Log failed request
                self._emit_request_log(
                    method, url, status, attempt,
                    time.monotonic() - t0, correlation_id, log_context,
                    error=bc_message,
                )

                # Raise appropriate error
                error_cls = _ERROR_MAP.get(status, BCLIError)
                kwargs: dict[str, Any] = {
                    "status_code": status,
                    "bc_message": bc_message,
                    "correlation_id": correlation_id,
                }
                if status == 429:
                    kwargs["retry_after"] = _get_retry_after(response)

                message = f"HTTP {status} {response.reason_phrase}: {method} {url}"
                hint = _hint_for_bc_error(status, bc_message, url)
                if hint:
                    message = f"{message}\n  Hint: {hint}"

                raise error_cls(message, **kwargs)

            except (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.RemoteProtocolError,
            ) as e:
                last_error = e
                # The most dangerous case for a mutation: the request may have
                # reached the server and been applied before the connection
                # dropped, so there is no way to know a retry is safe.
                if attempt < self._max_retries and retry_safe:
                    logger.warning(
                        "Network error on %s %s: %s, retrying in %.1fs",
                        method, url, e, backoff,
                    )
                    import asyncio
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                self._emit_request_log(
                    method, url, 0, attempt,
                    time.monotonic() - t0, None, log_context,
                    error=str(e),
                )
                raise ServerError(
                    f"Network error after {self._max_retries + 1} attempts: {e}",
                ) from e

        raise ServerError(f"Request failed after {self._max_retries + 1} attempts") from last_error

    @staticmethod
    def _emit_request_log(
        method: str,
        url: str,
        status: int,
        retry_count: int,
        latency_s: float,
        correlation_id: str | None,
        context: dict[str, Any] | None,
        *,
        error: str | None = None,
    ) -> None:
        """Emit structured JSON log for every HTTP request."""
        record: dict[str, Any] = {
            "event": "bc_http_request",
            "method": method,
            "url": url,
            "status": status,
            "retry_count": retry_count,
            "latency_ms": round(latency_s * 1000, 1),
            "correlation_id": correlation_id,
        }
        if context:
            record.update(context)
        if error:
            record["error"] = error
        _request_logger.info(json.dumps(record, default=str))

    # Convenience methods

    async def get(self, url: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        return await self._request("GET", url, params=params)

    async def get_absolute(self, url: str) -> dict[str, Any]:
        """GET with an absolute URL (e.g., nextLink).

        Validates the URL host before attaching auth, so a malicious
        ``@odata.nextLink`` returned by a compromised custom-API endpoint
        cannot redirect the bearer token to an attacker-controlled host.
        See :func:`bcli._url.assert_bc_origin`.
        """
        assert_bc_origin(url)
        return await self._request("GET", url)

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        params: dict[str, str] | None = None,
        log_context: dict[str, str] | None = None,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """Stream a GET response body to ``dest`` and return what was written.

        Read-only by construction: this issues a GET, never parses the body as
        JSON, and never goes through ``SafeContext`` — there is nothing to gate
        because nothing changes in BC.

        ``url`` is typically a ``@odata.mediaReadLink`` the *server* put in a
        response, so it runs through :func:`bcli._url.assert_bc_origin` before
        the bearer token is attached — same token-leak guard as
        :meth:`get_absolute`.

        Bytes land in a ``<dest>.<random>.part`` sibling and are moved onto
        ``dest`` with :func:`os.replace` once the stream completes, so a failed
        download leaves neither a truncated ``dest`` nor a stray part file.
        ``dest`` inherits the temp file's ``0600`` mode rather than the umask —
        a downloaded invoice is the account's data, not the machine's.

        Returns ``{"bytes_written", "content_type", "correlation_id"}``.
        """
        assert_bc_origin(url)

        # Deliberately a sibling of ``_request`` rather than a branch inside
        # it: that method buffers the whole response and calls
        # ``response.json()`` on success, which is precisely wrong for a media
        # stream. The retry *policy* is the one documented above ``_request``'s
        # loop; a GET can always be repeated, so this loop is unconditionally
        # retry-safe and needs no ``retry_safe`` gate. Only the body handling
        # differs.
        backoff = INITIAL_BACKOFF
        t0 = time.monotonic()
        last_error: Exception | None = None
        result: dict[str, Any] | None = None

        # One temp file for all attempts. Each attempt rewinds and truncates it
        # first: a retry that follows a partially-streamed response would
        # otherwise append the second body to the first half of the first.
        tmp = tempfile.NamedTemporaryFile(
            delete=False, dir=dest.parent, prefix=dest.name + ".", suffix=".part",
        )
        tmp_path = Path(tmp.name)

        try:
            with tmp as f:
                for attempt in range(self._max_retries + 1):
                    f.seek(0)
                    f.truncate()
                    try:
                        headers = await self._inject_auth()
                        # The client default is application/json; a media
                        # stream is whatever BC says it is.
                        headers["Accept"] = "*/*"

                        logger.debug("GET %s (stream, attempt %d)", url, attempt + 1)

                        wait: float | None = None
                        async with self._client.stream(
                            "GET", url, params=params, headers=headers,
                        ) as response:
                            correlation_id = response.headers.get(
                                "x-ms-correlation-request-id",
                            )

                            if response.is_success:
                                written = 0
                                async for chunk in response.aiter_bytes():
                                    f.write(chunk)
                                    written += len(chunk)
                                context: dict[str, Any] = dict(log_context or {})
                                context["bytes_written"] = written
                                self._emit_request_log(
                                    "GET", url, response.status_code, attempt,
                                    time.monotonic() - t0, correlation_id, context,
                                )
                                result = {
                                    "bytes_written": written,
                                    "content_type": response.headers.get("content-type"),
                                    "correlation_id": correlation_id,
                                }
                                break

                            # A streamed response has no body loaded yet, so
                            # the error payload has to be read before it can
                            # be parsed.
                            await response.aread()
                            bc_message, correlation_id = _parse_bc_error(response)
                            status = response.status_code

                            if status in _RETRYABLE and attempt < self._max_retries:
                                retry_after = _get_retry_after(response)
                                wait = retry_after if retry_after else backoff
                                logger.warning(
                                    "Retryable error %d on %s, waiting %.1fs (attempt %d/%d)",
                                    status, url, wait, attempt + 1, self._max_retries + 1,
                                )
                            else:
                                self._emit_request_log(
                                    "GET", url, status, attempt,
                                    time.monotonic() - t0, correlation_id, log_context,
                                    error=bc_message,
                                )
                                error_cls = _ERROR_MAP.get(status, BCLIError)
                                kwargs: dict[str, Any] = {
                                    "status_code": status,
                                    "bc_message": bc_message,
                                    "correlation_id": correlation_id,
                                }
                                if status == 429:
                                    kwargs["retry_after"] = _get_retry_after(response)
                                message = (
                                    f"HTTP {status} {response.reason_phrase}: GET {url}"
                                )
                                hint = _hint_for_bc_error(status, bc_message, url)
                                if hint:
                                    message = f"{message}\n  Hint: {hint}"
                                raise error_cls(message, **kwargs)

                        # Sleeping outside the ``async with`` releases the
                        # connection while we wait.
                        import asyncio
                        await asyncio.sleep(wait or backoff)
                        backoff *= 2
                        continue

                    except (
                        httpx.ConnectError,
                        httpx.ReadTimeout,
                        httpx.WriteTimeout,
                        httpx.RemoteProtocolError,
                        # A stream can also drop mid-body, which surfaces here
                        # rather than as a timeout.
                        httpx.ReadError,
                    ) as e:
                        last_error = e
                        if attempt < self._max_retries:
                            logger.warning(
                                "Network error on GET %s: %s, retrying in %.1fs",
                                url, e, backoff,
                            )
                            import asyncio
                            await asyncio.sleep(backoff)
                            backoff *= 2
                            continue
                        self._emit_request_log(
                            "GET", url, 0, attempt,
                            time.monotonic() - t0, None, log_context,
                            error=str(e),
                        )
                        raise ServerError(
                            f"Network error after {self._max_retries + 1} attempts: {e}",
                        ) from e

            if result is None:
                raise ServerError(
                    f"Download failed after {self._max_retries + 1} attempts",
                ) from last_error

            if overwrite:
                os.replace(tmp_path, dest)
            else:
                # No-replace publication. os.link raises FileExistsError if the
                # destination appeared after the CLI's pre-flight check — e.g. a
                # parent directory swapped to a symlink mid-download — so a race
                # can't silently clobber a file the user never agreed to replace.
                # The finally below removes the now-linked temp file.
                try:
                    os.link(tmp_path, dest)
                except FileExistsError:
                    raise FileExistsError(
                        f"Refusing to overwrite {dest}: it appeared after the "
                        f"pre-flight check. Re-run with --overwrite to replace it."
                    ) from None
            return result
        finally:
            # No-op once os.replace has moved it; on the no-replace path this
            # removes the source of the hardlink. Cleans up every failure path.
            tmp_path.unlink(missing_ok=True)

    async def post(
        self,
        url: str,
        *,
        json_body: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST", url, json_body=json_body, idempotency_key=idempotency_key,
        )

    async def patch(
        self,
        url: str,
        *,
        json_body: dict[str, Any],
        etag: str = "*",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", url, json_body=json_body, etag=etag,
            idempotency_key=idempotency_key,
        )

    async def patch_binary(
        self,
        url: str,
        *,
        content: bytes,
        content_type: str = "application/octet-stream",
        etag: str = "*",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """PATCH raw binary bytes with a custom Content-Type (e.g. attachments/content)."""
        return await self._request(
            "PATCH", url, content=content, content_type=content_type, etag=etag,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self,
        url: str,
        *,
        etag: str = "*",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE", url, etag=etag, idempotency_key=idempotency_key,
        )


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
