"""Authentication providers for the BC ETL source.

This module is part of the generic layer and must not import from bcli.*.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable, Protocol


class AuthProvider(Protocol):
    """Async token provider. Any object with `get_token()` works."""

    async def get_token(self) -> str: ...


class StaticTokenAuth:
    """Auth backed by a user-supplied token-fetching callback.

    Useful when you already have an authenticated client (e.g. bcli) and
    want to reuse its token acquisition logic.
    """

    def __init__(self, token_provider: Callable[[], Awaitable[str]]) -> None:
        self._provider = token_provider

    async def get_token(self) -> str:
        return await self._provider()


class ClientCredentialsAuth:
    """Standalone MSAL client-credentials auth.

    Uses msal.ConfidentialClientApplication with a 5-minute expiry buffer.
    Caches the token in memory for the life of the instance.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scope: str = "https://api.businesscentral.dynamics.com/.default",
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def get_token(self) -> str:
        if self._token and time.time() < self._expires_at - 300:
            return self._token

        # MSAL is sync; run in a thread if needed. For simplicity here we
        # block briefly — token acquisition is fast and rare.
        import msal

        app = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            client_credential=self._client_secret,
            authority=f"https://login.microsoftonline.com/{self._tenant_id}",
        )
        result = app.acquire_token_for_client(scopes=[self._scope])
        if "access_token" not in result:
            raise RuntimeError(
                f"Failed to acquire token: {result.get('error_description', result)}"
            )

        self._token = result["access_token"]
        self._expires_at = time.time() + result.get("expires_in", 3600)
        return self._token
