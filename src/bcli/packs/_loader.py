"""Load a :class:`Pack` from a directory containing ``pack.yaml`` + files.

The on-disk layout matches the plan (Part 1):

    packs/<name>/
      pack.yaml                  # manifest + index of contents
      fragments/*.md             # agent fragments
      queries/*.yaml             # saved-query bodies
      batches/*.yaml             # batch templates
      presets/*.json             # registry presets

``pack.yaml`` references files by relative path; the loader resolves
them and produces a fully populated :class:`Pack`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from bcli.packs._protocol import (
    AgentFragment,
    Pack,
    PackBatch,
    PackContents,
    PackManifest,
    PackQuery,
    PackRegistryPreset,
    TARGET_AGENTS,
    VALID_TARGETS,
)


class PackLoadError(Exception):
    """Raised when a pack directory can't be loaded into a :class:`Pack`."""


def load_pack(source: Path) -> Pack:
    """Read ``<source>/pack.yaml`` and return a :class:`Pack`.

    ``source`` may be either the pack directory itself or the
    ``pack.yaml`` file directly. All referenced content files are
    eagerly loaded so the installer can run against the in-memory
    object without re-touching disk.
    """
    source = Path(source)
    if source.is_dir():
        manifest_path = source / "pack.yaml"
        root = source
    elif source.is_file():
        manifest_path = source
        root = source.parent
    else:
        raise PackLoadError(f"pack source not found: {source}")

    if not manifest_path.is_file():
        raise PackLoadError(f"missing pack.yaml in {root}")

    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise PackLoadError(f"pack.yaml in {root} is not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise PackLoadError(f"pack.yaml in {root}: top-level must be a mapping")

    manifest = _parse_manifest(raw, source=manifest_path)
    contents = _parse_contents(raw, root=root, pack_name=manifest.name)
    return Pack(manifest=manifest, contents=contents, source_path=root)


def _parse_manifest(raw: dict[str, Any], *, source: Path) -> PackManifest:
    if "name" not in raw:
        raise PackLoadError(f"{source}: missing required field 'name'")
    if "version" not in raw:
        raise PackLoadError(f"{source}: missing required field 'version'")
    return PackManifest(
        name=str(raw["name"]),
        version=str(raw["version"]),
        description=str(raw.get("description", "")),
        target_profile=str(raw.get("target_profile", "")),
        recommended_context_providers=tuple(
            str(x) for x in raw.get("recommended_context_providers", []) or ()
        ),
    )


def _parse_contents(
    raw: dict[str, Any], *, root: Path, pack_name: str
) -> PackContents:
    block = raw.get("contents") or {}
    if not isinstance(block, dict):
        raise PackLoadError(f"{root}: 'contents' must be a mapping")

    fragments = _load_fragments(block.get("agent_fragments") or (), root=root)
    queries = _load_queries(block.get("queries") or (), root=root)
    batches = _load_batches(block.get("batches") or (), root=root)
    presets = _load_presets(block.get("registry_presets") or (), root=root)
    return PackContents(
        agent_fragments=tuple(fragments),
        queries=tuple(queries),
        batches=tuple(batches),
        registry_presets=tuple(presets),
    )


def _load_fragments(
    spec: Any, *, root: Path
) -> list[AgentFragment]:
    """Accepts either plain filename strings (`common-errors.md`) OR
    structured dicts with ``name`` / ``targets`` / ``description``."""
    out: list[AgentFragment] = []
    for entry in spec:
        if isinstance(entry, str):
            name = entry
            targets: tuple[str, ...] = (TARGET_AGENTS,)
            description = ""
        elif isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("file") or "")
            if not name:
                raise PackLoadError(
                    f"{root}: agent_fragment entry missing 'name'"
                )
            tgts = entry.get("targets") or [TARGET_AGENTS]
            if isinstance(tgts, str):
                tgts = [tgts]
            bad = [t for t in tgts if t not in VALID_TARGETS]
            if bad:
                raise PackLoadError(
                    f"{root}: fragment {name!r} has invalid targets {bad}; "
                    f"expected subset of {sorted(VALID_TARGETS)}"
                )
            targets = tuple(str(t) for t in tgts)
            description = str(entry.get("description", ""))
        else:
            raise PackLoadError(
                f"{root}: agent_fragment must be str or mapping, "
                f"got {type(entry).__name__}"
            )
        path = _resolve_fragment_path(root, name)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            raise PackLoadError(
                f"{root}: cannot read fragment {name!r} at {path}: {e}"
            ) from e
        out.append(AgentFragment(
            name=name,
            content=content,
            targets=targets,
            description=description,
        ))
    return out


def _resolve_fragment_path(root: Path, name: str) -> Path:
    """Fragments live under ``fragments/`` by convention, but a pack
    author may pass any relative path. We try both for robustness."""
    candidate = root / "fragments" / name
    if candidate.is_file():
        return candidate
    candidate = root / name
    if candidate.is_file():
        return candidate
    raise PackLoadError(
        f"{root}: fragment {name!r} not found under fragments/ or root"
    )


def _load_queries(spec: Any, *, root: Path) -> list[PackQuery]:
    """Accept a list of filenames OR a list of {name, file} dicts."""
    out: list[PackQuery] = []
    for entry in spec:
        if isinstance(entry, str):
            file = entry
        elif isinstance(entry, dict):
            file = str(entry.get("file") or entry.get("path") or "")
        else:
            raise PackLoadError(
                f"{root}: query entry must be str or mapping"
            )
        path = root / "queries" / file
        if not path.is_file():
            path = root / file
        if not path.is_file():
            raise PackLoadError(f"{root}: query file {file!r} not found")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise PackLoadError(
                f"{root}: query file {file!r} is not valid YAML: {e}"
            ) from e
        if not isinstance(data, dict):
            raise PackLoadError(
                f"{root}: query file {file!r}: expected mapping at top level"
            )
        # Each file may carry one or many queries — either a top-level
        # ``queries: {name: body}`` map or a single body with a ``name``.
        if "queries" in data and isinstance(data["queries"], dict):
            for name, body in data["queries"].items():
                if not isinstance(body, dict):
                    raise PackLoadError(
                        f"{root}: query {name!r} body must be a mapping"
                    )
                out.append(PackQuery(name=str(name), body=dict(body)))
        else:
            name = str(data.get("name") or Path(file).stem)
            body = {k: v for k, v in data.items() if k != "name"}
            out.append(PackQuery(name=name, body=body))
    return out


def _load_batches(spec: Any, *, root: Path) -> list[PackBatch]:
    out: list[PackBatch] = []
    for entry in spec:
        if isinstance(entry, str):
            file = entry
        elif isinstance(entry, dict):
            file = str(entry.get("file") or entry.get("path") or "")
        else:
            raise PackLoadError(
                f"{root}: batch entry must be str or mapping"
            )
        path = root / "batches" / file
        if not path.is_file():
            path = root / file
        if not path.is_file():
            raise PackLoadError(f"{root}: batch file {file!r} not found")
        body = path.read_text(encoding="utf-8")
        out.append(PackBatch(filename=Path(file).name, body=body))
    return out


def _load_presets(spec: Any, *, root: Path) -> list[PackRegistryPreset]:
    out: list[PackRegistryPreset] = []
    for entry in spec:
        if isinstance(entry, str):
            file = entry
        elif isinstance(entry, dict):
            file = str(entry.get("file") or entry.get("path") or "")
        else:
            raise PackLoadError(
                f"{root}: registry_preset entry must be str or mapping"
            )
        path = root / "presets" / file
        if not path.is_file():
            path = root / file
        if not path.is_file():
            raise PackLoadError(
                f"{root}: registry preset {file!r} not found"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise PackLoadError(
                f"{root}: registry preset {file!r} invalid JSON: {e}"
            ) from e
        if not isinstance(data, dict):
            raise PackLoadError(
                f"{root}: registry preset {file!r}: expected JSON object"
            )
        # A preset file holds one or more endpoints. Support both
        # forms: {"endpoints": {name: body}} and a single endpoint as
        # the top-level object.
        endpoints = data.get("endpoints")
        if isinstance(endpoints, dict):
            for name, body in endpoints.items():
                if not isinstance(body, dict):
                    raise PackLoadError(
                        f"{root}: preset endpoint {name!r}: body not a mapping"
                    )
                out.append(PackRegistryPreset(name=str(name), body=dict(body)))
        else:
            name = str(data.get("name") or Path(file).stem)
            body = {k: v for k, v in data.items() if k != "name"}
            out.append(PackRegistryPreset(name=name, body=body))
    return out


__all__ = ["PackLoadError", "load_pack"]
