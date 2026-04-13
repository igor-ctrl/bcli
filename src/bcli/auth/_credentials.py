"""Client credentials auth flow using MSAL."""

from __future__ import annotations

import logging
import os

import msal

from bcli.auth._token_cache import TokenCache
from bcli.config._defaults import BC_SCOPE, ENTRA_AUTHORITY_BASE
from bcli.errors import AuthError, ConfigError

logger = logging.getLogger(__name__)


def _try_keyring_get(service: str, username: str) -> str | None:
    """Try to get a secret from the OS keychain. Returns None if keyring unavailable."""
    try:
        import keyring

        return keyring.get_password(service, username)
    except Exception:
        return None


def _try_keyring_set(service: str, username: str, password: str) -> bool:
    """Try to store a secret in the OS keychain. Returns True on success."""
    try:
        import keyring

        keyring.set_password(service, username, password)
        return True
    except Exception:
        return False


def _try_keyring_delete(service: str, username: str) -> bool:
    """Try to delete a secret from the OS keychain."""
    try:
        import keyring

        keyring.delete_password(service, username)
        return True
    except Exception:
        return False


KEYRING_SERVICE = "bcli"


class ClientCredentialsAuth:
    """Service-to-service auth via OAuth2 client credentials flow.

    Secret resolution is lazy — only resolved when a new token is needed.
    Resolution order:
    1. Direct client_secret parameter
    2. OS keychain (via keyring library)
    3. Environment variable (client_secret_env)
    4. Error
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
        self._client_secret = client_secret  # May be None — resolved lazily
        self._client_secret_env = client_secret_env
        self._authority = f"{ENTRA_AUTHORITY_BASE}/{tenant_id}"

    def _resolve_secret(self) -> str:
        """Resolve the client secret. Only called when a new token is needed."""
        # 1. Already provided directly
        if self._client_secret:
            return self._client_secret

        # 2. Try OS keychain
        keyring_key = f"{self._tenant_id}:{self._client_id}"
        secret = _try_keyring_get(KEYRING_SERVICE, keyring_key)
        if secret:
            logger.debug("Resolved client secret from OS keychain")
            return secret

        # 3. Try environment variable
        if self._client_secret_env:
            secret = os.environ.get(self._client_secret_env)
            if secret:
                return secret

        # 4. Try generic fallback env var
        secret = os.environ.get("BCLI_CLIENT_SECRET") or os.environ.get("BCLI_SECRET")
        if secret:
            return secret

        # Nothing found
        hints = []
        hints.append("bcli auth store-secret  (saves to OS keychain)")
        if self._client_secret_env:
            hints.append(f"export {self._client_secret_env}=<secret>")
        raise ConfigError(
            "No client secret found. Options:\n  " + "\n  ".join(hints)
        )

    async def get_access_token(self) -> str:
        """Get a valid access token, using cache if available."""
        # Check disk cache first — no secret needed
        cached = self._token_cache.get(self._tenant_id, self._client_id)
        if cached:
            return cached

        # Need a new token — resolve secret now
        secret = self._resolve_secret()

        app = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            authority=self._authority,
            client_credential=secret,
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

        self._token_cache.put(self._tenant_id, self._client_id, access_token, expires_in)

        logger.info("Acquired new BC API access token")
        return access_token

    def clear_cache(self) -> None:
        """Clear cached tokens for this tenant/client."""
        self._token_cache.clear(self._tenant_id, self._client_id)

    @staticmethod
    def store_secret(tenant_id: str, client_id: str, secret: str) -> bool:
        """Store a client secret in the OS keychain."""
        keyring_key = f"{tenant_id}:{client_id}"
        return _try_keyring_set(KEYRING_SERVICE, keyring_key, secret)

    @staticmethod
    def delete_secret(tenant_id: str, client_id: str) -> bool:
        """Delete a client secret from the OS keychain."""
        keyring_key = f"{tenant_id}:{client_id}"
        return _try_keyring_delete(KEYRING_SERVICE, keyring_key)

    @staticmethod
    def has_keyring() -> bool:
        """Check if the keyring library is available."""
        try:
            import keyring  # noqa: F401

            return True
        except ImportError:
            return False
