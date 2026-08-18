"""Device code auth flow for interactive CLI use."""

from __future__ import annotations

import logging
import sys

import msal

from bcli.auth._msal_cache import MsalTokenCache
from bcli.auth._token_cache import TokenCache
from bcli.config._defaults import BC_SCOPE, ENTRA_AUTHORITY_BASE
from bcli.errors import AuthError

logger = logging.getLogger(__name__)


class DeviceCodeAuth:
    """Interactive device code flow — user authenticates via browser.

    Used for CLI interactive sessions where the user is present.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        token_cache: TokenCache | None = None,
        msal_cache: MsalTokenCache | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._token_cache = token_cache or TokenCache()
        self._msal_cache = msal_cache or MsalTokenCache()
        self._authority = f"{ENTRA_AUTHORITY_BASE}/{tenant_id}"

    async def get_access_token(self) -> str:
        """Get a valid access token, using cache or device code flow."""
        # Check disk cache first
        cached = self._token_cache.get(self._tenant_id, self._client_id)
        if cached:
            return cached

        # Build MSAL public client (no client_secret needed). token_cache is
        # what makes the silent path below work at all: without it MSAL keeps
        # its cache in memory, so a fresh process has no account and no refresh
        # token, and every expiry costs the user another device-code prompt.
        app = msal.PublicClientApplication(
            client_id=self._client_id,
            authority=self._authority,
            token_cache=self._msal_cache.cache,
        )

        # Try silent acquisition first — now backed by the refresh token
        # persisted from a previous invocation, not just this process.
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(
                scopes=[BC_SCOPE],
                account=accounts[0],
            )
            if result and "access_token" in result:
                # A silent refresh usually rotates the refresh token; persist.
                self._msal_cache.save()
                self._cache_token(result)
                logger.info("Renewed BC API token silently (no prompt)")
                return result["access_token"]

        # Initiate device code flow
        flow = app.initiate_device_flow(scopes=[BC_SCOPE])

        if "user_code" not in flow:
            raise AuthError(
                f"Device code flow failed: {flow.get('error_description', 'Unknown error')}",
                status_code=401,
            )

        # Print the device code message for the user
        print(f"\n{flow['message']}\n", file=sys.stderr)

        # Block until user completes browser auth
        result = app.acquire_token_by_device_flow(flow)

        if "access_token" not in result:
            error_desc = result.get("error_description", result.get("error", "Unknown error"))
            raise AuthError(f"Device code auth failed: {error_desc}", status_code=401)

        self._msal_cache.save()
        self._cache_token(result)
        logger.info("Acquired BC API token via device code flow")
        return result["access_token"]

    def _cache_token(self, result: dict) -> None:
        """Cache the token to disk."""
        access_token = result["access_token"]
        expires_in = result.get("expires_in", 3600)
        self._token_cache.put(self._tenant_id, self._client_id, access_token, expires_in)

    def clear_cache(self) -> None:
        """Clear cached tokens for this tenant/client.

        Clears the persisted MSAL cache too. Dropping only the access token
        would leave the refresh token on disk, so a "logged out" user could
        still renew silently.
        """
        self._token_cache.clear(self._tenant_id, self._client_id)
        self._msal_cache.remove_accounts(
            client_id=self._client_id, authority=self._authority
        )
