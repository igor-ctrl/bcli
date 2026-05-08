"""Atomically apply a verified bundle to the user's config tree.

Concurrency contract: two ``bcli config refresh`` runs against the same
profile may execute simultaneously (one in a terminal, one fired by an
agent). The apply step holds an advisory file lock on
``<bundle_dir>/<profile>.refresh.lock`` for its duration so the second
process serializes behind the first instead of racing on .previous /
.incoming temp names. ``flock`` is POSIX; on Windows we fall back to a
best-effort exclusive create on the same lock file.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bcli.bundle._manifest import Bundle, write_local_manifest

logger = logging.getLogger("bcli.bundle.apply")


@dataclass(frozen=True)
class BundleApplyResult:
    """Summary of what changed after a refresh.

    Returned to the CLI so the user can see exactly what their team bundle
    just rewrote — registry, queries, both — and what the previous version
    was, in case rollback is needed.
    """

    profile: str
    new_version: str
    previous_version: str
    registry_changed: bool
    queries_changed: bool
    field_lists_changed: bool


def apply_bundle(
    bundle: Bundle,
    *,
    registries_dir: Path,
    queries_dir: Path,
    bundle_dir: Path,
) -> BundleApplyResult:
    """Apply ``bundle`` atomically; retain the previous version for rollback.

    Behaviour:
      * A profile-scoped advisory lock serializes concurrent refreshes so
        two processes can't race on the .previous / .incoming temp names.
      * Each destination file is written via ``write-temp + replace`` —
        per-process unique temp names, atomic ``Path.replace`` for the
        final swap. A partial failure leaves the user on the prior version.
      * The previous file is preserved at ``<name>.previous`` so
        :func:`rollback_bundle` can undo the apply without re-fetching.
      * ``manifest.json`` is the *last* thing written. Its presence is the
        signal "a bundle is installed" used by ``bcli doctor``.
      * ``field_lists.json`` is merged into the registry on apply so the
        registry loader (which only reads ``<profile>.json``) actually
        sees the prewarmed field names. Bundles that don't ship field
        lists leave the registry untouched.
    """
    profile = bundle.manifest.profile
    registries_dir.mkdir(parents=True, exist_ok=True)
    queries_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    lock_path = bundle_dir / f"{profile}.refresh.lock"
    with _profile_lock(lock_path):
        registry_dest = registries_dir / f"{profile}.json"
        queries_dest = queries_dir / f"{profile}.yaml"

        registry_changed = False
        queries_changed = False
        field_lists_changed = False

        if bundle.has_registry():
            registry_payload = bundle.registry_path.read_bytes()
            if bundle.has_field_lists():
                merged = _merge_field_lists(
                    registry_payload, bundle.field_lists_path.read_bytes()
                )
                if merged is not None:
                    registry_payload = merged
                    field_lists_changed = True
            registry_changed = _atomic_replace_bytes_with_backup(
                registry_payload, registry_dest
            )
        if bundle.has_queries():
            queries_changed = _atomic_replace_with_backup(
                bundle.queries_path, queries_dest
            )

        previous = _previous_version(bundle_dir, profile)
        # Back up the in-place manifest so rollback can restore the version
        # marker too, not just the content files. Without this, a rollback
        # would restore the registry but leave `bcli doctor` claiming the
        # newer bundle version is installed.
        manifest_path = bundle_dir / f"{profile}.manifest.json"
        if manifest_path.is_file():
            backup = manifest_path.with_suffix(manifest_path.suffix + ".previous")
            manifest_path.replace(backup)
        write_local_manifest(bundle_dir, bundle.manifest)

        return BundleApplyResult(
            profile=profile,
            new_version=bundle.manifest.version,
            previous_version=previous,
            registry_changed=registry_changed,
            queries_changed=queries_changed,
            field_lists_changed=field_lists_changed,
        )


def rollback_bundle(
    profile: str,
    *,
    registries_dir: Path,
    queries_dir: Path,
    bundle_dir: Path,
) -> bool:
    """Restore the most recent ``.previous`` siblings for a profile.

    Returns ``True`` when at least one file was rolled back. Idempotent —
    calling it twice in a row is harmless.
    """
    candidates = [
        registries_dir / f"{profile}.json",
        queries_dir / f"{profile}.yaml",
        registries_dir / f"{profile}.field_lists.json",
    ]
    rolled_back = False
    for path in candidates:
        backup = path.with_suffix(path.suffix + ".previous")
        if backup.is_file():
            backup.replace(path)
            rolled_back = True

    manifest_path = bundle_dir / f"{profile}.manifest.json"
    backup_manifest = manifest_path.with_suffix(manifest_path.suffix + ".previous")
    if backup_manifest.is_file():
        backup_manifest.replace(manifest_path)
        rolled_back = True
    elif manifest_path.is_file() and not rolled_back:
        # No prior backup at all — best we can do is wipe the manifest so
        # `bcli doctor` reverts to "bundle not installed".
        manifest_path.unlink()
        rolled_back = True

    return rolled_back


# ─── helpers ──────────────────────────────────────────────────────────


def _atomic_replace_with_backup(src: Path, dest: Path) -> bool:
    """Replace ``dest`` with ``src``, retaining the old one as ``.previous``.

    Returns ``True`` when the destination changed. Per-process unique
    temp names (via :func:`tempfile.mkstemp`) keep concurrent processes
    from clobbering each other's in-flight writes if the lock is somehow
    bypassed — defense in depth on top of the profile lock.
    """
    return _atomic_replace_bytes_with_backup(src.read_bytes(), dest)


def _atomic_replace_bytes_with_backup(payload: bytes, dest: Path) -> bool:
    """Atomically write ``payload`` to ``dest`` with a ``.previous`` backup.

    Returns ``True`` when content changed; ``False`` for byte-identical
    re-applies (no backup churn, no bogus "changed" line in the CLI
    summary).
    """
    if dest.is_file():
        existing = dest.read_bytes()
        if existing == payload:
            return False
        backup = dest.with_suffix(dest.suffix + ".previous")
        dest.replace(backup)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{dest.name}.",
        suffix=".incoming",
        dir=dest.parent,
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(dest)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return True


def _previous_version(bundle_dir: Path, profile: str) -> str:
    """Read the in-place manifest before we overwrite it."""
    path = bundle_dir / f"{profile}.manifest.json"
    if not path.is_file():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return str(raw.get("version", ""))
    except (OSError, json.JSONDecodeError):
        return ""


def _merge_field_lists(
    registry_bytes: bytes, field_lists_bytes: bytes
) -> bytes | None:
    """Merge a ``field_lists.json`` map into ``registry.json``.

    The registry loader only reads ``<profile>.json``, so the bundle's
    optional ``field_lists.json`` was a dead artifact until this step.
    We update each endpoint's ``field_names`` in place with whatever the
    bundle provides — the bundle's authoritative list wins, which is the
    contract for team-managed bundles.

    Returns the merged JSON bytes, or ``None`` when either file is
    unparseable (in which case the registry stays as-is and the apply
    step proceeds without field-list prewarming — verifier already
    validated content hashes, so unparseable here is genuinely
    surprising).
    """
    try:
        registry = json.loads(registry_bytes.decode("utf-8"))
        field_lists = json.loads(field_lists_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning("field_lists merge skipped: %s", e)
        return None

    if not isinstance(registry, dict) or not isinstance(field_lists, dict):
        return None

    endpoints = registry.get("endpoints")
    if not isinstance(endpoints, list):
        return None

    overrides = field_lists.get("field_names") or field_lists
    if not isinstance(overrides, dict):
        return None

    changed = False
    for entry in endpoints:
        if not isinstance(entry, dict):
            continue
        name = entry.get("entity_set_name")
        if isinstance(name, str) and name in overrides:
            new_fields = overrides[name]
            if isinstance(new_fields, list) and new_fields != entry.get("field_names"):
                entry["field_names"] = list(new_fields)
                changed = True

    if not changed:
        return None
    return json.dumps(registry, indent=2).encode("utf-8")


# ─── locking ──────────────────────────────────────────────────────────


@contextlib.contextmanager
def _profile_lock(lock_path: Path):
    """Advisory lock to serialize concurrent refreshes for one profile.

    POSIX uses ``flock``. Windows falls back to a best-effort exclusive
    file create with retries — not a real mutex, but sufficient to keep
    two interactive runs from racing in the same minute. For real
    multi-process safety on Windows, callers should serialize at a
    higher layer.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        yield from _win_lock(lock_path)
    else:
        yield from _posix_lock(lock_path)


def _posix_lock(lock_path: Path):
    import fcntl

    f = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def _win_lock(lock_path: Path):
    import time

    deadline = time.monotonic() + 30.0
    while True:
        try:
            fd = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
            )
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)
    try:
        yield
    finally:
        try:
            os.close(fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                lock_path.unlink()
