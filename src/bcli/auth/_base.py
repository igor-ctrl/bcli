"""Auth provider protocol."""

from __future__ import annotations

from typing import Protocol


class AuthProvider(Protocol):
    """Protocol for authentication providers."""

    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        ...

    def clear_cache(self) -> None:
        """Clear any cached tokens."""
        ...
