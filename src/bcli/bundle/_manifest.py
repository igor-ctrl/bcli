"""Bundle manifest schema and on-disk representation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


class BundleVerifyError(Exception):
    """Raised when a bundle fails checksum or signature verification.

    Always re-raise to abort the apply step; never silently fall back to
    "use it anyway." The trust story for team bundles is the only thing
    keeping a malicious upstream from injecting a custom endpoint pointing
    at an attacker-controlled BC tenant.
    """


class BundleManifest(BaseModel):
    """The ``manifest.json`` that lives at the root of every bundle.

    Schema is intentionally narrow: anything an admin needs to hand-edit
    sits at the top level, anything bcli computes (checksum) is required
    but plain. Forward-compatibility is handled via ``schema_version``;
    bumps are major-version events for the bundle format itself, not for
    bcli.
    """

    schema_version: int = 1
    profile: str
    version: str
    published_at: datetime
    publisher: str = ""
    checksum_sha256: str
    signature: str = ""  # detached signature, optional today
    min_bcli_version: str = ""
    previous_version: str = ""
    release_notes: str = ""
    contents: dict[str, str] = Field(default_factory=dict)
    """Map of relative paths inside the bundle to their SHA-256 — populated
    at publish time so we can detect partial-extraction corruption without
    re-hashing the whole tarball."""

    model_config = {"extra": "allow"}


@dataclass(frozen=True)
class Bundle:
    """An extracted bundle ready to be applied."""

    manifest: BundleManifest
    root: Path
    """Filesystem directory containing manifest.json plus the bundle's
    files. Caller owns cleanup; the apply step does not delete the root."""

    @property
    def registry_path(self) -> Path:
        return self.root / "registry.json"

    @property
    def queries_path(self) -> Path:
        return self.root / "queries.yaml"

    @property
    def field_lists_path(self) -> Path:
        return self.root / "field_lists.json"

    def has_registry(self) -> bool:
        return self.registry_path.is_file()

    def has_queries(self) -> bool:
        return self.queries_path.is_file()

    def has_field_lists(self) -> bool:
        return self.field_lists_path.is_file()


def load_local_manifest(bundle_dir: Path, profile: str) -> BundleManifest | None:
    """Load the currently-applied manifest for ``profile``, if any.

    Returns ``None`` when the user has never run ``bcli config refresh``
    for this profile. Returns ``None`` (rather than raising) on a corrupt
    manifest so ``bcli doctor`` can flag it instead of crashing.
    """
    path = bundle_dir / f"{profile}.manifest.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return BundleManifest.model_validate(raw)
    except (OSError, ValueError):
        return None


def write_local_manifest(bundle_dir: Path, manifest: BundleManifest) -> Path:
    """Write the applied manifest atomically.

    Uses ``write-temp + replace`` so a concurrent ``bcli doctor`` reading
    the manifest never sees a half-written file. ``Path.replace`` is
    atomic on POSIX and Windows, so a racing reader sees either the old
    or the new manifest, never an empty / truncated one.
    """
    import os
    import tempfile

    bundle_dir.mkdir(parents=True, exist_ok=True)
    final = bundle_dir / f"{manifest.profile}.manifest.json"
    payload = manifest.model_dump_json(indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{manifest.profile}.manifest.",
        suffix=".tmp",
        dir=bundle_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(final)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return final
