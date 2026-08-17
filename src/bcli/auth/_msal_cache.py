"""Persistent MSAL token cache, so refresh tokens survive between invocations.

``BrowserAuth`` and ``DeviceCodeAuth`` both call ``acquire_token_silent()``
before falling back to an interactive prompt. That call can only ever succeed
if MSAL has an account and a refresh token to work with — and MSAL keeps those
in a cache that is **in-memory by default**. Construct
``msal.PublicClientApplication`` without ``token_cache=`` and every new process
starts blind: ``get_accounts()`` returns ``[]``, the silent path is dead code,
and the user re-authenticates interactively as soon as the ~1h access token in
:class:`bcli.auth.TokenCache` expires.

Persisting MSAL's own cache fixes that. The refresh token is reused silently and
the interactive prompt drops to roughly once per refresh-token lifetime.

Security posture
----------------
This file holds a **refresh token**, which is a longer-lived credential than
anything in ``tokens.json``. It is written through
:func:`bcli.auth._secure_io.write_secret_file` — atomic replace, ``0600``, in a
``0700`` parent — the same path the access-token cache already uses. That is a
deliberate consistency choice rather than a new mechanism.

We do **not** reach for ``msal_extensions`` (OS-keychain-backed persistence).
It would encrypt at rest on macOS/Windows, but it is not currently a bcli
dependency, its persistence backends vary by platform, and on headless Linux it
degrades to a plain file anyway. Adding it is a defensible follow-up; it is not
required to fix the re-auth defect and would widen the dependency surface of a
tool that ships to a managed fleet.
"""

from __future__ import annotations

import logging
from pathlib import Path

import msal

from bcli.auth._secure_io import warn_if_insecure_perms, write_secret_file
from bcli.config._defaults import MSAL_CACHE_FILE

logger = logging.getLogger(__name__)


class MsalTokenCache:
    """Disk-backed wrapper around ``msal.SerializableTokenCache``.

    Pass :attr:`cache` to ``msal.PublicClientApplication(token_cache=...)``,
    then call :meth:`save` after any token acquisition. ``save()`` is a no-op
    when MSAL didn't change anything, so it is cheap to call unconditionally.
    """

    def __init__(self, cache_file: Path | None = None) -> None:
        self._file = cache_file or MSAL_CACHE_FILE
        self._cache: msal.SerializableTokenCache | None = None

    @property
    def cache(self) -> msal.SerializableTokenCache:
        """The live MSAL cache, deserialized from disk on first access."""
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    def _load(self) -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        if not self._file.is_file():
            return cache

        warn_if_insecure_perms(self._file)
        try:
            cache.deserialize(self._file.read_text(encoding="utf-8"))
        except Exception as exc:
            # A truncated, hand-edited or version-skewed cache must degrade to
            # "you need to sign in again", never to a traceback on every
            # command. Deliberately broad: MSAL does not document a single
            # exception type for deserialize failures.
            logger.debug("Ignoring unreadable MSAL cache %s: %s", self._file, exc)
        return cache

    def save(self) -> bool:
        """Persist the cache if MSAL mutated it. Returns True if written."""
        cache = self.cache
        if not cache.has_state_changed:
            return False

        write_secret_file(self._file, cache.serialize())
        # MSAL clears this flag only inside its own persistence helpers, so we
        # clear it here — otherwise every later save() rewrites the same bytes.
        cache.has_state_changed = False
        return True

    def clear(self) -> None:
        """Delete the persisted cache and reset the in-memory copy."""
        self._cache = msal.SerializableTokenCache()
        try:
            self._file.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("Could not remove MSAL cache %s: %s", self._file, exc)

    def remove_accounts(self, *, client_id: str, authority: str) -> int:
        """Sign out every cached account for this client, and persist.

        Used by ``bcli auth logout``. Dropping the access token alone would
        leave the refresh token on disk, so a "logged out" user could keep
        minting tokens silently — this closes that.

        Returns the number of accounts removed. On any failure we fall back to
        deleting the whole cache file: over-logging-out is the safe direction.
        """
        try:
            app = msal.PublicClientApplication(
                client_id=client_id,
                authority=authority,
                token_cache=self.cache,
                # Keep logout usable offline. Instance discovery would other-
                # wise reach login.microsoftonline.com just to forget a local
                # credential.
                instance_discovery=False,
            )
            accounts = app.get_accounts()
            for account in accounts:
                app.remove_account(account)

            if accounts:
                # remove_account() mutates the cache but does not reliably set
                # has_state_changed, so write unconditionally here.
                write_secret_file(self._file, self.cache.serialize())
                self.cache.has_state_changed = False
            return len(accounts)
        except Exception as exc:
            logger.debug(
                "Falling back to deleting %s after remove_accounts failed: %s",
                self._file,
                exc,
            )
            self.clear()
            return 0

    def has_accounts(self, *, client_id: str, authority: str) -> bool:
        """True if a silent refresh could plausibly succeed. Best-effort."""
        try:
            app = msal.PublicClientApplication(
                client_id=client_id,
                authority=authority,
                token_cache=self.cache,
                instance_discovery=False,
            )
            return bool(app.get_accounts())
        except Exception as exc:
            logger.debug("Could not inspect MSAL cache: %s", exc)
            return False
