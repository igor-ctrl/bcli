"""Endpoint registry with three-tier resolution."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from bcli.config._defaults import REGISTRIES_DIR
from bcli.registry._schema import EndpointMetadata


class EndpointRegistry:
    """Registry that resolves endpoint names to API routes.

    Resolution order:
    1. Custom registry (user-imported, per-profile)
    2. Standard v2.0 registry (ships with package)
    """

    def __init__(
        self,
        profile_name: str | None = None,
        *,
        disable_standard: bool = False,
        allowed_categories: list[str] | None = None,
        allowed_endpoints: list[str] | None = None,
    ) -> None:
        self._standard: dict[str, EndpointMetadata] = {}
        self._custom: dict[str, EndpointMetadata] = {}
        self._allowed_categories = {c.lower() for c in allowed_categories} if allowed_categories else None
        self._allowed_endpoints = {e.lower() for e in allowed_endpoints} if allowed_endpoints else None
        if not disable_standard:
            self._load_standard()
        if profile_name:
            self._load_custom(profile_name)

    def _load_standard(self) -> None:
        """Load the built-in standard v2.0 registry."""
        data_file = resources.files("bcli.registry").joinpath("standard_v2.json")
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

    def _is_allowed(self, meta: EndpointMetadata) -> bool:
        """Check if an endpoint passes the allowlist filters."""
        if self._allowed_endpoints and meta.entity_set_name.lower() in self._allowed_endpoints:
            return True  # Explicitly allowed by name always passes
        if self._allowed_categories and meta.category.lower() not in self._allowed_categories:
            return False  # Category not in allowlist
        return True

    def get(self, entity_set_name: str) -> EndpointMetadata | None:
        """Look up an endpoint. Custom takes priority over standard."""
        key = entity_set_name.lower()
        result = self._custom.get(key) or self._standard.get(key)
        if result and not self._is_allowed(result):
            return None
        return result

    def resolve(self, entity_set_name: str) -> EndpointMetadata:
        """Look up an endpoint, raising RegistryError if not found."""
        result = self.get(entity_set_name)
        if result is None:
            from bcli.errors import RegistryError

            suggestions = self.search(entity_set_name)[:3]
            hint = ""
            if suggestions:
                names = ", ".join(s.entity_set_name for s in suggestions)
                hint = f" Did you mean: {names}?"
            raise RegistryError(
                f"Endpoint '{entity_set_name}' not found in any registry.{hint}"
                " Run 'bcli registry import' to add custom APIs,"
                " or pass --publisher/--group/--version to target a custom API route"
                " (this does NOT reach Microsoft's standard v2.0 entities)."
            )
        return result

    def search(self, query: str) -> list[EndpointMetadata]:
        """Fuzzy search across all registries."""
        query_lower = query.lower()
        results: list[tuple[int, EndpointMetadata]] = []

        for meta in [*self._custom.values(), *self._standard.values()]:
            if not self._is_allowed(meta):
                continue
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
            results.extend(sorted(
                (m for m in self._custom.values() if self._is_allowed(m)),
                key=lambda m: m.entity_set_name,
            ))
        if not custom_only:
            results.extend(sorted(
                (m for m in self._standard.values() if self._is_allowed(m)),
                key=lambda m: m.entity_set_name,
            ))
        return results

    @property
    def standard_count(self) -> int:
        return len(self._standard)

    @property
    def custom_count(self) -> int:
        return len(self._custom)
