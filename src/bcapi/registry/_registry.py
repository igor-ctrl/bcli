"""Endpoint registry with three-tier resolution."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from bcapi.config._defaults import REGISTRIES_DIR
from bcapi.registry._schema import EndpointMetadata


class EndpointRegistry:
    """Registry that resolves endpoint names to API routes.

    Resolution order:
    1. Custom registry (user-imported, per-profile)
    2. Standard v2.0 registry (ships with package)
    """

    def __init__(self, profile_name: str | None = None) -> None:
        self._standard: dict[str, EndpointMetadata] = {}
        self._custom: dict[str, EndpointMetadata] = {}
        self._load_standard()
        if profile_name:
            self._load_custom(profile_name)

    def _load_standard(self) -> None:
        """Load the built-in standard v2.0 registry."""
        data_file = resources.files("bcapi.registry").joinpath("standard_v2.json")
        raw = json.loads(data_file.read_text(encoding="utf-8"))
        for entry in raw.get("entities", []):
            meta = EndpointMetadata.model_validate(entry)
            self._standard[meta.entity_set_name.lower()] = meta

    def _load_custom(self, profile_name: str) -> None:
        """Load user-imported custom registry for a profile."""
        registry_file = REGISTRIES_DIR / f"{profile_name}.json"
        if not registry_file.is_file():
            return
        raw = json.loads(registry_file.read_text(encoding="utf-8"))
        for entry in raw.get("endpoints", []):
            meta = EndpointMetadata.model_validate(entry)
            self._custom[meta.entity_set_name.lower()] = meta

    def load_custom_from_file(self, path: Path) -> int:
        """Load custom endpoints from an arbitrary JSON file. Returns count loaded."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        count = 0
        for entry in raw.get("endpoints", []):
            meta = EndpointMetadata.model_validate(entry)
            self._custom[meta.entity_set_name.lower()] = meta
            count += 1
        return count

    def get(self, entity_set_name: str) -> EndpointMetadata | None:
        """Look up an endpoint. Custom takes priority over standard."""
        key = entity_set_name.lower()
        return self._custom.get(key) or self._standard.get(key)

    def resolve(self, entity_set_name: str) -> EndpointMetadata:
        """Look up an endpoint, raising RegistryError if not found."""
        result = self.get(entity_set_name)
        if result is None:
            from bcapi.errors import RegistryError

            suggestions = self.search(entity_set_name)[:3]
            hint = ""
            if suggestions:
                names = ", ".join(s.entity_set_name for s in suggestions)
                hint = f" Did you mean: {names}?"
            raise RegistryError(
                f"Endpoint '{entity_set_name}' not found in any registry.{hint}"
                " Run 'bcapi registry import' to add custom APIs,"
                " or use --publisher/--group/--version for ad-hoc access."
            )
        return result

    def search(self, query: str) -> list[EndpointMetadata]:
        """Fuzzy search across all registries."""
        query_lower = query.lower()
        results: list[tuple[int, EndpointMetadata]] = []

        for meta in [*self._custom.values(), *self._standard.values()]:
            score = 0
            name_lower = meta.entity_set_name.lower()
            desc_lower = meta.description.lower()

            if query_lower == name_lower:
                score = 100
            elif query_lower in name_lower:
                score = 80
            elif query_lower in desc_lower:
                score = 40
            elif query_lower in meta.category.lower():
                score = 30

            if score > 0:
                results.append((score, meta))

        results.sort(key=lambda x: (-x[0], x[1].entity_set_name))
        return [meta for _, meta in results]

    def list_all(self, *, custom_only: bool = False, standard_only: bool = False) -> list[EndpointMetadata]:
        """List all registered endpoints."""
        results: list[EndpointMetadata] = []
        if not standard_only:
            results.extend(sorted(self._custom.values(), key=lambda m: m.entity_set_name))
        if not custom_only:
            results.extend(sorted(self._standard.values(), key=lambda m: m.entity_set_name))
        return results

    @property
    def standard_count(self) -> int:
        return len(self._standard)

    @property
    def custom_count(self) -> int:
        return len(self._custom)
