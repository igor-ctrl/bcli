"""Auth provider for an access token the caller already holds.

Every other provider in this package *acquires* a token — by opening a browser,
by polling a device-code flow, or by exchanging a client secret. Some embedders
have a token already and only need the SDK to use it:

* a hosted service that performs an on-behalf-of exchange per request, so the BC
  call runs as the signed-in user rather than as the service;
* a CI job handed a token by its platform;
* a script or notebook pasting one in for a one-off.

None of those can use the browser flow, which opens a browser and binds a
loopback listener on whichever machine runs the process.

Pass either a fixed string or a supplier. Prefer a **supplier** in any
long-running process: it is re-invoked on every call, so a refreshed token is
picked up instead of a pinned one expiring mid-session. Nothing is cached here —
the caller owns the token's lifetime, which is why :meth:`clear_cache` is a
no-op.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from bcli.errors import ConfigError

TokenSupplier = Callable[[], "str | Awaitable[str]"]


class StaticTokenAuth:
    """Supply a pre-acquired bearer token. Satisfies the ``AuthProvider`` protocol.

    Args:
        token: The access token, or a callable returning one (sync or async).
    """

    def __init__(self, token: str | TokenSupplier) -> None:
        if callable(token):
            self._supplier: TokenSupplier | None = token
            self._token: str | None = None
        else:
            self._supplier = None
            self._token = self._validated(token)

    @staticmethod
    def _validated(raw: object) -> str:
        """Reject the two mistakes that would otherwise surface as a puzzling 401."""
        if not isinstance(raw, str):
            raise ConfigError(
                f"Access token must be a string, got {type(raw).__name__}. "
                "A token supplier must return the raw token."
            )
        token = raw.strip()
        if not token:
            raise ConfigError(
                "Access token is empty. Pass the raw bearer token, or a callable "
                "that returns one."
            )
        if token.lower().startswith("bearer "):
            raise ConfigError(
                "Access token must not include the 'Bearer ' prefix — the transport "
                "adds it, so this would send 'Authorization: Bearer Bearer ...'."
            )
        return token

    async def get_access_token(self) -> str:
        """Return the token, re-invoking the supplier if one was given."""
        if self._supplier is None:
            # Validated in __init__; narrowing for type checkers.
            assert self._token is not None
            return self._token

        supplied = self._supplier()
        if inspect.isawaitable(supplied):
            supplied = await supplied
        return self._validated(supplied)

    def clear_cache(self) -> None:
        """No-op — nothing is cached. Supply a callable if you need invalidation."""
        return None
