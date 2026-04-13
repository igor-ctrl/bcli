"""Disk-based token cache for persistent auth across CLI invocations."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from bcli.config._defaults import TOKEN_CACHE_FILE

logger = logging.getLogger(__name__)


class TokenCache:
    """Simple disk-based token cache keyed by (tenant_id, client_id)."""

    def __init__(self, cache_file: Path | None = None) -> None:
        self._file = cache_file or TOKEN_CACHE_FILE
        self._data: dict | None = None

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        if self._file.is_file():
            try:
                self._data = json.loads(self._file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}
        return self._data

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(self._data or {}, indent=2))

    def _cache_key(self, tenant_id: str, client_id: str) -> str:
        return f"{tenant_id}:{client_id}"

    def get(self, tenant_id: str, client_id: str) -> str | None:
        """Get a cached token if it's still valid (5-min buffer)."""
        data = self._load()
        key = self._cache_key(tenant_id, client_id)
        entry = data.get(key)
        if not entry:
            return None

        expires_at = datetime.fromisoformat(entry["expires_at"])
        now = datetime.now(timezone.utc)
        buffer = 300  # 5 minutes

        if (expires_at.timestamp() - now.timestamp()) > buffer:
            logger.debug("Using cached token for %s", key)
            return entry["access_token"]

        logger.debug("Cached token expired for %s", key)
        return None

    def put(self, tenant_id: str, client_id: str, access_token: str, expires_in: int) -> None:
        """Cache a token."""
        data = self._load()
        key = self._cache_key(tenant_id, client_id)
        expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        data[key] = {
            "access_token": access_token,
            "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        self._data = data
        self._save()

    def clear(self, tenant_id: str | None = None, client_id: str | None = None) -> None:
        """Clear cached tokens. If no args, clear all."""
        if tenant_id and client_id:
            data = self._load()
            key = self._cache_key(tenant_id, client_id)
            data.pop(key, None)
            self._data = data
            self._save()
        else:
            self._data = {}
            self._save()
