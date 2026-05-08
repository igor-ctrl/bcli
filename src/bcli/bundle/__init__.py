"""Team-shared registry / saved-query bundles.

A bundle is a tarball published by an admin to a known HTTPS location and
pulled by team members with ``bcli config refresh``. The format is a thin
contract on top of the existing per-profile JSON / YAML files: the bundle
ships a ``manifest.json`` plus the same ``registry.json`` and
``queries.yaml`` shapes the user already has on disk, so an admin can
hand-author a bundle without learning a new schema.

Phase 2 ships the format, fetch / verify / apply / rollback primitives, and
the ``bcli config refresh`` command. Cryptographic signing is gated behind
a ``Verifier`` protocol: today the default is a SHA-256 checksum (covers
in-flight tampering on a trusted CDN), and a real signing scheme
(``minisign`` or ``cosign``) plugs into the same seam once the team picks
one.
"""

from bcli.bundle._apply import BundleApplyResult, apply_bundle, rollback_bundle
from bcli.bundle._fetch import BundleFetchError, fetch_bundle
from bcli.bundle._manifest import (
    Bundle,
    BundleManifest,
    BundleVerifyError,
    load_local_manifest,
)
from bcli.bundle._verify import (
    NullVerifier,
    Sha256Verifier,
    Verifier,
    verify_bundle,
)

__all__ = [
    "Bundle",
    "BundleApplyResult",
    "BundleFetchError",
    "BundleManifest",
    "BundleVerifyError",
    "NullVerifier",
    "Sha256Verifier",
    "Verifier",
    "apply_bundle",
    "fetch_bundle",
    "load_local_manifest",
    "rollback_bundle",
    "verify_bundle",
]
