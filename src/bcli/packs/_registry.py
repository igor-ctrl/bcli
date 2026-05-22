"""Pack discovery — built-in + entry-point + local path (R8).

The registry is the source of truth for "what packs can I install?"
The OSS package ships two built-ins (``starter-generic``,
``cronus-demo``); third-party packages register via the
``bcli.packs`` entry-point group; ``bcli pack install --path <dir>``
loads from a local directory for development.

Entry-point providers register a callable (no args) that returns a
:class:`Pack`. This lets downstream packages keep their pack content
inside their own wheel rather than vendoring it into the OSS repo.
"""

from __future__ import annotations

import logging
from importlib import resources
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Iterator

from bcli.packs._loader import PackLoadError, load_pack
from bcli.packs._protocol import Pack

logger = logging.getLogger("bcli.packs")

# Group name third-party packages register under.
ENTRYPOINT_GROUP = "bcli.packs"


def builtin_packs_dir() -> Path | None:
    """Resolve the ``packs/`` directory shipped in the wheel.

    Returns ``None`` if the directory doesn't exist — discovery
    treats that as "no built-ins available" without crashing.

    Lookup order:

    1. ``packs/`` next to the bcli source tree (editable install).
    2. ``bcli/packs/_builtin`` shipped inside the wheel (the
       ``[tool.hatch.build.targets.wheel.force-include]`` mapping
       in ``pyproject.toml``).
    """
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "packs"
    if candidate.is_dir():
        return candidate

    # Wheel install: hatch force-includes packs/ -> bcli/packs/_builtin.
    here = Path(__file__).resolve().parent
    wheel_builtin = here / "_builtin"
    if wheel_builtin.is_dir():
        return wheel_builtin

    # Final fallback: importlib.resources for any future layout.
    try:
        ref = resources.files("bcli.packs").joinpath("_builtin")
        if ref.is_dir():
            return Path(str(ref))
    except (ModuleNotFoundError, AttributeError, FileNotFoundError):
        pass
    return None


def discover_builtin_packs(root: Path | None = None) -> list[Pack]:
    """Scan ``root`` (defaults to :func:`builtin_packs_dir`) for packs.

    A *pack directory* is any immediate subdirectory containing a
    ``pack.yaml``. Subdirectories without one are ignored.
    """
    root = root or builtin_packs_dir()
    if root is None or not root.is_dir():
        return []
    out: list[Pack] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "pack.yaml").is_file():
            continue
        try:
            out.append(load_pack(child))
        except PackLoadError as e:
            logger.warning("Skipping built-in pack %s: %s", child, e)
    return out


def discover_entrypoint_packs() -> list[Pack]:
    """Run every ``bcli.packs`` entry-point and collect the returned packs.

    A failing provider logs a warning and is skipped — one broken
    third-party pack must not block the registry.
    """
    out: list[Pack] = []
    for ep in _iter_entrypoints():
        try:
            provider = ep.load()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bcli.packs entry-point %r failed to load: %s", ep.name, exc
            )
            continue
        try:
            pack = provider() if callable(provider) else provider
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bcli.packs entry-point %r raised: %s", ep.name, exc
            )
            continue
        if not isinstance(pack, Pack):
            logger.warning(
                "bcli.packs entry-point %r did not return a Pack (got %s)",
                ep.name, type(pack).__name__,
            )
            continue
        out.append(pack)
    return out


def _iter_entrypoints() -> Iterator[EntryPoint]:
    try:
        eps = entry_points(group=ENTRYPOINT_GROUP)
    except Exception:  # pragma: no cover — defensive
        return
    yield from eps


def discover_all(*, builtin_root: Path | None = None) -> dict[str, Pack]:
    """Return ``{name: Pack}`` of every known pack — built-in + plugin.

    On name collision the later source wins, so a third-party pack
    can override a built-in by registering the same name (rare but
    sometimes desirable for Beautech-style overlays).
    """
    out: dict[str, Pack] = {}
    for p in discover_builtin_packs(builtin_root):
        out[p.name] = p
    for p in discover_entrypoint_packs():
        out[p.name] = p
    return out


__all__ = [
    "ENTRYPOINT_GROUP",
    "builtin_packs_dir",
    "discover_all",
    "discover_builtin_packs",
    "discover_entrypoint_packs",
]
