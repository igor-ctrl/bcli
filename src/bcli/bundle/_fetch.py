"""Fetch a bundle tarball over HTTPS and extract it to a temp directory.

Hardening notes — every limit here exists because the threat model is a
compromised CDN serving a malicious tarball to a finance laptop:

* **Allowed file set.** Only ``manifest.json``, ``registry.json``,
  ``queries.yaml``, ``field_lists.json``, and ``README.md`` are accepted.
  Anything else aborts extraction.
* **No symlinks, no hardlinks, no devices.** Bundles ship plain files.
* **Bounded sizes.** Compressed payload, decompressed total, per-member,
  and member count all have hard caps. A small archive that decompresses
  to gigabytes is the canonical attack — bound at every layer.
* **Path traversal.** Member names are normalized; absolute paths and
  ``..`` segments fail closed.

These limits are deliberately set above any plausible legitimate bundle
(typical bundle is well under 1MB) but well below "OOM the laptop." Adjust
upward only after measuring real bundle sizes in production.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from bcli.bundle._manifest import Bundle, BundleManifest

logger = logging.getLogger("bcli.bundle.fetch")

# Hard ceilings. A real finance/engine bundle is small (KB-MB range);
# anything outside these is either misconfigured or hostile.
MAX_COMPRESSED_BYTES = 25 * 1024 * 1024  # 25 MB on the wire
MAX_TOTAL_EXTRACTED_BYTES = 100 * 1024 * 1024  # 100 MB after gunzip
MAX_PER_MEMBER_BYTES = 50 * 1024 * 1024  # 50 MB single file
MAX_MEMBER_COUNT = 64

# Allowlist of files the apply step actually consumes. Extra files in the
# tar are not just wasted bytes — they widen the attack surface for path
# tricks and surprise content.
_ALLOWED_BUNDLE_PATHS = frozenset(
    {
        "manifest.json",
        "registry.json",
        "queries.yaml",
        "field_lists.json",
        "README.md",
    }
)


class BundleFetchError(Exception):
    """Network, redirect, or extraction failure during bundle pull."""


def fetch_bundle(
    url: str,
    *,
    timeout: float = 30.0,
    auth_header: str | None = None,
    allow_file_url: bool | None = None,
) -> tuple[Bundle, bytes]:
    """Download ``url``, extract it, and hand back the raw bytes.

    ``allow_file_url`` is the dev-only escape hatch for ``file://`` URLs.
    Production callers must leave it ``None`` (the default), which only
    accepts ``file://`` when ``BCLI_DEV=1`` is set in the environment.
    Without that, only ``https://`` is allowed — the team contract.
    """
    parsed = urlparse(url)
    file_allowed = (
        allow_file_url
        if allow_file_url is not None
        else os.environ.get("BCLI_DEV") in ("1", "true", "yes")
    )
    if parsed.scheme == "file":
        if not file_allowed:
            raise BundleFetchError(
                "file:// URLs are dev-only; set BCLI_DEV=1 to opt in. "
                "Production refresh expects an https:// URL pointing at a "
                "signed bundle in your team's blob storage."
            )
    elif parsed.scheme != "https":
        raise BundleFetchError(
            f"refusing to fetch over {parsed.scheme!r} — only https:// is "
            "accepted (signed-HTTPS distribution is the team contract)"
        )

    headers = {"User-Agent": "bcli-bundle/1.0"}
    if auth_header:
        headers["Authorization"] = auth_header

    try:
        if parsed.scheme == "file":
            raw = Path(parsed.path).read_bytes()
        else:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                raw = resp.content
    except (httpx.HTTPError, OSError) as e:
        raise BundleFetchError(f"could not download {url}: {e}") from e

    if len(raw) > MAX_COMPRESSED_BYTES:
        raise BundleFetchError(
            f"bundle is {len(raw)} bytes, exceeds the {MAX_COMPRESSED_BYTES}-byte "
            "compressed-size limit (gzip-bomb defense)"
        )

    extract_root = Path(tempfile.mkdtemp(prefix="bcli-bundle-"))
    try:
        bundle = _extract(raw, extract_root)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(extract_root, ignore_errors=True)
        if isinstance(e, BundleFetchError):
            raise
        raise BundleFetchError(f"could not extract bundle: {e}") from e

    return bundle, raw


def _extract(raw: bytes, dest: Path) -> Bundle:
    """Extract a tarball into ``dest`` with size + path + type guards."""
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
        members = tar.getmembers()
        if len(members) > MAX_MEMBER_COUNT:
            raise BundleFetchError(
                f"bundle has {len(members)} members, max is {MAX_MEMBER_COUNT}"
            )

        total = 0
        for m in members:
            _check_safe_member(m)
            if m.size > MAX_PER_MEMBER_BYTES:
                raise BundleFetchError(
                    f"member '{m.name}' is {m.size} bytes, max is "
                    f"{MAX_PER_MEMBER_BYTES} (per-file decompression-bomb defense)"
                )
            total += m.size
            if total > MAX_TOTAL_EXTRACTED_BYTES:
                raise BundleFetchError(
                    f"bundle would extract to >{MAX_TOTAL_EXTRACTED_BYTES} bytes "
                    "(decompression-bomb defense)"
                )

        try:
            tar.extractall(dest, filter="data")
        except TypeError:
            tar.extractall(dest)  # noqa: S202 — pre-validated above

    manifest_path = dest / "manifest.json"
    if not manifest_path.is_file():
        raise BundleFetchError("bundle is missing manifest.json at the root")

    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = BundleManifest.model_validate(raw_manifest)
    return Bundle(manifest=manifest, root=dest)


def _check_safe_member(member: tarfile.TarInfo) -> None:
    """Reject anything that isn't a plain file at an allowlisted path.

    Defense-in-depth even though the verifier will catch most tampering
    later: an extraction-time crash is cheaper to recover from than a
    half-applied bundle, and the early reject keeps malicious tarballs
    from touching disk at all.
    """
    if not (member.isreg() or member.isdir()):
        raise BundleFetchError(
            f"unsafe member type in bundle: {member.name} "
            f"(type={member.type!r}; only regular files and dirs allowed)"
        )
    if member.issym() or member.islnk() or member.isdev():
        # Belt-and-suspenders — issym/islnk/isdev are subsumed by !isreg
        # above but the check is cheap and the message is clearer.
        raise BundleFetchError(f"links/devices not allowed: {member.name}")

    name = Path(member.name)
    if name.is_absolute() or ".." in name.parts:
        raise BundleFetchError(f"unsafe path in bundle: {member.name}")

    if member.isdir():
        return  # directories don't need allowlist checks
    rel = name.as_posix()
    if rel not in _ALLOWED_BUNDLE_PATHS:
        raise BundleFetchError(
            f"unexpected file in bundle: {rel!r} "
            f"(allowed: {sorted(_ALLOWED_BUNDLE_PATHS)})"
        )
