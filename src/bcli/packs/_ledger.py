"""Pack install ledger (R2).

Each install writes a JSON ledger at
``~/.config/bcli/packs/<profile>/<pack>.json`` describing *every*
artefact the install produced. Uninstall trusts the ledger first,
marker pairs second — that lets us catch drift like "the ledger
says we wrote N artefacts at path P but only N-1 markers remain"
without forcing the user to clean up by hand.

Schema (v1):

    {
      "schema_version": "1.0",
      "pack_name": "starter-generic",
      "pack_version": "0.1.0",
      "installed_at": "2026-05-22T10:00:00+00:00",
      "profile": "production",
      "target": "/abs/path/to/install/target",
      "paths": [
        {
          "kind": "query" | "batch" | "fragment_file" | "registry_preset"
                | "agents_block" | "claude_block",
          "path": "/abs/path",
          "block_id": "bcli-pack:starter-generic:common-errors.md"
                       (only for *_block entries),
          "rendered_hash": "sha256:...",
          "owner": "starter-generic"
        }
      ],
      "registry_endpoints": [
        {"name": "myEntity", "rendered_hash": "sha256:..."}
      ],
      "recommended_context_providers": ["beautech"]
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bcli.packs._protocol import Pack

logger = logging.getLogger("bcli.packs")

_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class LedgerEntry:
    """One artefact the installer wrote."""

    kind: str
    path: str
    rendered_hash: str
    owner: str
    block_id: str = ""


@dataclass(frozen=True)
class LedgerRegistryEntry:
    """One endpoint definition this pack installed into the custom registry."""

    name: str
    rendered_hash: str
    owner: str


@dataclass(frozen=True)
class Ledger:
    """A pack's install record."""

    pack_name: str
    pack_version: str
    installed_at: str
    profile: str
    target: str
    paths: tuple[LedgerEntry, ...] = ()
    registry_endpoints: tuple[LedgerRegistryEntry, ...] = ()
    recommended_context_providers: tuple[str, ...] = ()
    schema_version: str = _SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "pack_name": self.pack_name,
            "pack_version": self.pack_version,
            "installed_at": self.installed_at,
            "profile": self.profile,
            "target": self.target,
            "paths": [
                {
                    "kind": p.kind,
                    "path": p.path,
                    "rendered_hash": p.rendered_hash,
                    "owner": p.owner,
                    "block_id": p.block_id,
                }
                for p in self.paths
            ],
            "registry_endpoints": [
                {
                    "name": e.name,
                    "rendered_hash": e.rendered_hash,
                    "owner": e.owner,
                }
                for e in self.registry_endpoints
            ],
            "recommended_context_providers": list(
                self.recommended_context_providers
            ),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Ledger":
        paths = tuple(
            LedgerEntry(
                kind=str(p.get("kind", "")),
                path=str(p.get("path", "")),
                rendered_hash=str(p.get("rendered_hash", "")),
                owner=str(p.get("owner", "")),
                block_id=str(p.get("block_id", "")),
            )
            for p in raw.get("paths", []) or []
        )
        endpoints = tuple(
            LedgerRegistryEntry(
                name=str(e.get("name", "")),
                rendered_hash=str(e.get("rendered_hash", "")),
                owner=str(e.get("owner", "")),
            )
            for e in raw.get("registry_endpoints", []) or []
        )
        return cls(
            pack_name=str(raw.get("pack_name", "")),
            pack_version=str(raw.get("pack_version", "")),
            installed_at=str(raw.get("installed_at", "")),
            profile=str(raw.get("profile", "")),
            target=str(raw.get("target", "")),
            paths=paths,
            registry_endpoints=endpoints,
            recommended_context_providers=tuple(
                str(x)
                for x in raw.get("recommended_context_providers", []) or ()
            ),
            schema_version=str(raw.get("schema_version", _SCHEMA_VERSION)),
        )


# ─── Path resolution + I/O ──────────────────────────────────────────


def _config_dir() -> Path:
    return Path.home() / ".config" / "bcli"


def ledger_dir(profile: str, *, config_dir: Path | None = None) -> Path:
    return (config_dir or _config_dir()) / "packs" / profile


def ledger_path(
    pack: str, profile: str, *, config_dir: Path | None = None
) -> Path:
    return ledger_dir(profile, config_dir=config_dir) / f"{pack}.json"


def read_ledger(
    pack: str, profile: str, *, config_dir: Path | None = None
) -> Ledger | None:
    path = ledger_path(pack, profile, config_dir=config_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug("could not read ledger %s: %s", path, e)
        return None
    if not isinstance(raw, dict):
        return None
    return Ledger.from_dict(raw)


def write_ledger(
    ledger: Ledger, *, config_dir: Path | None = None
) -> Path | None:
    """Atomically persist ``ledger``. Returns the path on success."""
    path = ledger_path(
        ledger.pack_name, ledger.profile, config_dir=config_dir
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(ledger.to_dict(), indent=2, sort_keys=False)
        fd, tmp = tempfile.mkstemp(
            prefix=path.name + ".", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path
    except (OSError, TypeError, ValueError) as e:
        logger.warning("could not write pack ledger %s: %s", path, e)
        return None


def delete_ledger(
    pack: str, profile: str, *, config_dir: Path | None = None
) -> bool:
    path = ledger_path(pack, profile, config_dir=config_dir)
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError as e:
        logger.warning("could not delete pack ledger %s: %s", path, e)
        return False


def list_ledgers(
    profile: str, *, config_dir: Path | None = None
) -> list[Ledger]:
    """Return every ledger under the given profile."""
    folder = ledger_dir(profile, config_dir=config_dir)
    if not folder.is_dir():
        return []
    out: list[Ledger] = []
    for path in sorted(folder.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict):
            out.append(Ledger.from_dict(raw))
    return out


# ─── Hash helpers ───────────────────────────────────────────────────


def hash_content(value: str | bytes) -> str:
    """Stable sha256 hex of ``value`` (text or bytes)."""
    data = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(data).hexdigest()


def build_ledger(
    pack: Pack,
    *,
    profile: str,
    target: Path,
    paths: list[LedgerEntry],
    registry_endpoints: list[LedgerRegistryEntry] | None = None,
) -> Ledger:
    """Convenience constructor — pulls timestamp + recommendations from the pack."""
    return Ledger(
        pack_name=pack.name,
        pack_version=pack.version,
        installed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        profile=profile,
        target=str(target),
        paths=tuple(paths),
        registry_endpoints=tuple(registry_endpoints or []),
        recommended_context_providers=tuple(
            pack.manifest.recommended_context_providers
        ),
    )


__all__ = [
    "Ledger",
    "LedgerEntry",
    "LedgerRegistryEntry",
    "build_ledger",
    "delete_ledger",
    "hash_content",
    "ledger_dir",
    "ledger_path",
    "list_ledgers",
    "read_ledger",
    "write_ledger",
]
