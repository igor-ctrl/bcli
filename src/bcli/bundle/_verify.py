"""Bundle verification — content checksum today, pluggable signature later.

.. warning::

    :class:`Sha256Verifier` is **not** an authenticity check. It proves
    that the bundle on disk is internally consistent (each file matches
    its declared hash, and the contents-roll-up hash matches the manifest
    field). It does **not** prove the bundle came from the publisher.

    A compromised CDN can mint a malicious ``registry.json``, recompute
    the per-file hashes, recompute the roll-up, and pass verification.
    Until a real cryptographic signer (minisign / cosign / ed25519 +
    pinned public key) lands at this seam, the team must distribute
    bundles only via private blob storage with org-level auth and an
    HTTPS-only contract. **Do not roll out to production without that
    constraint, or without landing the signing upgrade.**

The :class:`Verifier` protocol is the seam for cryptographic signing.
A real signer plugs in here without touching apply / fetch / publish —
refusing to ship phase 2 without a signer would have blocked the rest
of the rollout on a tooling decision that doesn't need to be made yet,
but tracking that decision as a phase-2-extension ship-blocker (see
``docs/plans/team-deployment.md`` "Risks") is mandatory.

Why content hashes instead of "hash the tarball wire bytes": tarballs
encode file mtimes, ownership, and ordering, so the wire bytes aren't
deterministic across publish runs unless every TarInfo header is pinned.
Hashing per-file contents and sorting by path gives a deterministic
result that survives re-archiving while still catching any file-level
tampering.
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from bcli.bundle._manifest import Bundle, BundleVerifyError


class Verifier(Protocol):
    """Pluggable interface for bundle authenticity checks.

    An implementation must be safe to call repeatedly and must raise
    :class:`BundleVerifyError` on any failure — never return False, never
    log-and-continue.
    """

    def verify(self, bundle: Bundle, raw_archive: bytes | None = None) -> None: ...


class NullVerifier:
    """Skip verification entirely. Only acceptable for local development."""

    def verify(self, bundle: Bundle, raw_archive: bytes | None = None) -> None:
        return None


class Sha256Verifier:
    """Per-file content hash + canonical roll-up.

    Verifies that every file in the extracted bundle matches the SHA-256
    in the manifest's ``contents`` map, then recomputes the canonical
    roll-up over those entries and compares it to ``checksum_sha256``.
    Either step failing means a tampered or corrupt bundle.
    """

    def verify(self, bundle: Bundle, raw_archive: bytes | None = None) -> None:
        manifest = bundle.manifest
        if not manifest.contents:
            raise BundleVerifyError(
                "manifest.contents is empty; refusing to apply unverified bundle"
            )
        expected_roll = (manifest.checksum_sha256 or "").lower().strip()
        if not expected_roll:
            raise BundleVerifyError(
                "manifest.checksum_sha256 is missing; refusing to apply"
            )

        # Validate the contents map *before* opening any files. A
        # malicious manifest can name `../etc/passwd` and we'd happily
        # `bundle.root / rel` our way out of the extraction directory.
        # Reject absolute paths, traversal, and anything outside the
        # allowlist of files the apply step actually consumes.
        for rel in manifest.contents:
            _check_safe_relpath(rel)

        for rel, expected_hash in sorted(manifest.contents.items()):
            file_path = bundle.root / rel
            try:
                resolved = file_path.resolve(strict=True)
                root_resolved = bundle.root.resolve(strict=True)
                resolved.relative_to(root_resolved)
            except (FileNotFoundError, ValueError) as e:
                raise BundleVerifyError(
                    f"manifest declares '{rel}' but the file is not safely "
                    f"contained in the bundle root ({e})"
                ) from e
            if not resolved.is_file():
                raise BundleVerifyError(
                    f"manifest declares '{rel}' but the file is not in the bundle"
                )
            actual = _hash_bytes(resolved.read_bytes())
            if actual != expected_hash:
                # Truncate hashes in error messages so a constant-time
                # comparison would not be needed — we don't leak enough
                # to grind a collision.
                raise BundleVerifyError(
                    f"file '{rel}' content mismatch: manifest declared "
                    f"{expected_hash[:12]}…, got {actual[:12]}…"
                )

        roll = canonical_roll_hash(manifest.contents)
        if roll != expected_roll:
            raise BundleVerifyError(
                f"manifest checksum mismatch: declared {expected_roll[:12]}…, "
                f"computed {roll[:12]}… — manifest itself is tampered"
            )


_ALLOWED_CONTENT_PATHS = frozenset(
    {"registry.json", "queries.yaml", "field_lists.json", "README.md"}
)


def _check_safe_relpath(rel: str) -> None:
    """Reject manifest paths that are absolute, traverse, or unknown."""
    from pathlib import PurePosixPath

    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts:
        raise BundleVerifyError(f"unsafe content path in manifest: {rel!r}")
    if rel not in _ALLOWED_CONTENT_PATHS:
        raise BundleVerifyError(
            f"unexpected content path in manifest: {rel!r} "
            f"(allowed: {sorted(_ALLOWED_CONTENT_PATHS)})"
        )


def verify_bundle(
    bundle: Bundle,
    *,
    verifier: Verifier,
    raw_archive: bytes | None = None,
) -> None:
    """Run the configured verifier; re-raise on failure."""
    verifier.verify(bundle, raw_archive=raw_archive)


def canonical_roll_hash(contents: dict[str, str]) -> str:
    """Hash the canonical-JSON form of the contents map.

    Both the publisher and the verifier compute this the same way: sorted
    keys, no whitespace, ASCII-safe encoding. That makes the value
    reproducible regardless of the dict-iteration order on either side.
    """
    canonical = json.dumps(
        {k: v for k, v in sorted(contents.items())},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
