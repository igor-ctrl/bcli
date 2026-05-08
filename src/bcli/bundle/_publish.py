"""Author-side helper: build a signed bundle tarball from a directory tree.

Lives in the SDK so admins can call it programmatically. The CLI surface
is ``bcli config make-bundle <dir>`` (registered alongside ``refresh``).
"""

from __future__ import annotations

import hashlib
import tarfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from bcli.bundle._manifest import BundleManifest
from bcli.bundle._verify import canonical_roll_hash


def make_bundle(
    source_dir: Path,
    *,
    profile: str,
    version: str,
    publisher: str = "",
    release_notes: str = "",
    previous_version: str = "",
    output_path: Path | None = None,
) -> tuple[Path, BundleManifest]:
    """Tar up ``source_dir`` and produce a signed-ish bundle.

    ``source_dir`` should contain at least ``registry.json``; ``queries.yaml``
    and ``field_lists.json`` are optional. The function:

    1. Collects per-file SHA-256s into ``manifest.contents``.
    2. Builds the tarball in memory.
    3. Computes the archive checksum.
    4. Re-writes the manifest with the final checksum.
    5. Re-builds the tarball with the final manifest.
    6. Writes the result to ``output_path`` (or ``<profile>-<version>.tar.gz``).

    The two-pass build (build / hash / re-build) keeps the manifest's
    ``checksum_sha256`` referring to the *final* archive bytes — anything
    else would either force the verifier to know which member to skip or
    leave the user with a checksum that never matches.
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"bundle source dir not found: {source_dir}")

    files = _collect_files(source_dir)
    if not files:
        raise ValueError(f"no files found under {source_dir}")

    contents = {rel: _hash_bytes(b) for rel, b in files}
    roll = canonical_roll_hash(contents)

    manifest = BundleManifest(
        profile=profile,
        version=version,
        published_at=datetime.now(timezone.utc),
        publisher=publisher,
        checksum_sha256=roll,
        previous_version=previous_version,
        release_notes=release_notes,
        contents=contents,
    )

    archive = _build_archive(files, manifest)
    out = output_path or Path.cwd() / f"{profile}-{version}.tar.gz"
    out.write_bytes(archive)
    return out, manifest


# ─── helpers ──────────────────────────────────────────────────────────


def _collect_files(source_dir: Path) -> list[tuple[str, bytes]]:
    """Walk ``source_dir`` and return [(relpath, bytes)] sorted by path."""
    out: list[tuple[str, bytes]] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        # Skip anything that looks like build leftovers; admins shouldn't
        # accidentally publish their .DS_Store or editor swap files.
        if path.name.startswith(".") or path.name.endswith(("~", ".swp")):
            continue
        if path.name == "manifest.json":
            # Authors hand-write contents.json siblings; the manifest is
            # generated, not consumed.
            continue
        rel = str(path.relative_to(source_dir))
        out.append((rel, path.read_bytes()))
    return out


def _build_archive(files: list[tuple[str, bytes]], manifest: BundleManifest) -> bytes:
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, BytesIO(manifest_bytes))
        for rel, data in files:
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, BytesIO(data))
    return buf.getvalue()


def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
