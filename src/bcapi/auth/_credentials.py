"""Client credentials auth flow using MSAL."""

from __future__ import annotations

import logging
import os

import msal

from bcapi.auth._token_cache import TokenCache
from bcapi.config._defaults import BC_SCOPE, ENTRA_AUTHORITY_BASE
from bcapi.errors import AuthError, ConfigError

logger = logging.getLogger(__name__)


class ClientCredentialsAuth:
    """Service-to-service auth via OAuth2 client credentials flow.

    Ported from /Users/igor/Projects/dags/utils/bc/bc_odata_client.py
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str | None = None,
        client_secret_env: str | None = None,
        token_cache: TokenCache | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._token_cache = token_cache or TokenCache()

        # Resolve secret: direct value or env var reference
        if client_secret:
            self._client_secret = client_secret
        elif client_secret_env:
            self._client_secret = os.environ.get(client_secret_env, "")
            if not self._client_secret:
                raise ConfigError(
                    f"Environment variable '{client_secret_env}' is not set."
                    " Set it or use 'client_secret' directly."
                )
        else:
            raise ConfigError("Either client_secret or client_secret_env must be provided.")

        self._authority = f"{ENTRA_AUTHORITY_BASE}/{tenant_id}"

    async def get_access_token(self) -> str:
        """Get a valid access token, using cache if available."""
        # Check disk cache first
        cached = self._token_cache.get(self._tenant_id, self._client_id)
        if cached:
            return cached

        # Acquire new token via MSAL
        app = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            authority=self._authority,
            client_credential=self._client_secret,
        )

        result = app.acquire_token_for_client(scopes=[BC_SCOPE])

        if "access_token" not in result:
            error_desc = result.get("error_description", result.get("error", "Unknown error"))
            raise AuthError(
                f"Failed to acquire token: {error_desc}",
                status_code=401,
            )

        access_token = result["access_token"]
        expires_in = result.get("expires_in", 3600)

        # Cache to disk
        self._token_cache.put(self._tenant_id, self._client_id, access_token, expires_in)

        logger.info("Acquired new BC API access token")
        return access_token

    def clear_cache(self) -> None:
        """Clear cached tokens for this tenant/client."""
        self._token_cache.clear(self._tenant_id, self._client_id)
