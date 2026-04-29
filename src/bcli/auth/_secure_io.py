"""Helpers for writing auth-sensitive files with private (0600) permissions.

Token caches, WorkOS identity caches, and anything else that contains a
bearer credential go through these helpers so the on-disk artefact isn't
left readable by other local users on shared / permissive-umask systems.

Behaviour:

* The parent directory is created with mode ``0o700`` if it doesn't exist.
* The payload is written to a sibling ``.tmp`` path, ``chmod`` to
  ``0o600``, and ``os.replace``-d into place — so the final file always
  exists either with old content or new content, never a half-written
  blank file.
* If an existing file is found with looser permissions (e.g. an upgrade
  from a previous bcli version), we warn once and tighten it.

Windows note: ``os.chmod`` on Windows only models the read-only bit,
not POSIX-style group/other separation. The threat model on Windows is
the user account itself, so this is best-effort there. The atomic-rename
and 0o600 behaviours work as documented on macOS / Linux.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_DIR_MODE = 0o700
_FILE_MODE = 0o600

# Bits we consider unsafe on a secret file (anything readable / writable
# by group or other). On Windows these checks are no-ops.
_INSECURE_FILE_MASK = 0o077
# Same for parent directories.
_INSECURE_DIR_MASK = 0o077

_warned_paths: set[str] = set()


def _is_posix() -> bool:
    return os.name == "posix"


def _warn_once(key: str, message: str) -> None:
    if key in _warned_paths:
        return
    _warned_paths.add(key)
    print(f"bcli: {message}", file=sys.stderr)


def warn_if_insecure_perms(path: Path) -> None:
    """Print a one-shot stderr warning if ``path`` is readable by others.

    Used at file-load time so a user upgrading from a pre-fix bcli is
    informed that a previously-loose token cache was tightened.
    """
    if not _is_posix() or not path.exists():
        return
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if mode & _INSECURE_FILE_MASK:
        _warn_once(
            str(path),
            f"{path} had loose permissions ({oct(mode)}); tightening to "
            f"{oct(_FILE_MODE)}. Other local users may have been able to "
            f"read cached credentials before this bcli upgrade.",
        )
        try:
            os.chmod(path, _FILE_MODE)
        except OSError as e:
            logger.debug("Could not chmod %s: %s", path, e)


def _ensure_private_dir(directory: Path) -> None:
    """Create ``directory`` (and parents) with ``0o700`` if it's missing.

    If it exists with looser perms, tighten it and warn once.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if not _is_posix():
        return
    try:
        mode = directory.stat().st_mode & 0o777
    except OSError:
        return
    if mode & _INSECURE_DIR_MASK:
        _warn_once(
            str(directory),
            f"{directory} had loose permissions ({oct(mode)}); tightening "
            f"to {oct(_DIR_MODE)}.",
        )
        try:
            os.chmod(directory, _DIR_MODE)
        except OSError as e:
            logger.debug("Could not chmod %s: %s", directory, e)


def write_secret_file(path: Path, content: str) -> None:
    """Atomically write ``content`` to ``path`` with private (0600) perms.

    Steps:
      1. Ensure parent dir exists at 0o700.
      2. Write payload to a sibling ``<name>.tmp`` path.
      3. ``chmod`` the temp file to 0o600 *before* the rename (so a reader
         who races us can never see a 0o644 version of the secret).
      4. ``os.replace`` the temp file into place — atomic on POSIX and
         Windows.
      5. ``chmod`` the final path too, in case the platform reset bits
         during the replace.
    """
    _ensure_private_dir(path.parent)

    tmp = path.with_suffix(path.suffix + ".tmp")

    # If a previous run crashed mid-write, drop the leftover.
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass

    # Open with explicit flags so the file is created with 0o600 from the
    # start on POSIX. Without this, the umask determines the initial mode
    # and there's a race window before the chmod.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(tmp), flags, _FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    if _is_posix():
        try:
            os.chmod(tmp, _FILE_MODE)
        except OSError as e:
            logger.debug("Could not chmod %s: %s", tmp, e)

    os.replace(tmp, path)

    if _is_posix():
        try:
            os.chmod(path, _FILE_MODE)
        except OSError as e:
            logger.debug("Could not chmod %s: %s", path, e)
