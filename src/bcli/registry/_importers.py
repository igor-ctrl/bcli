"""Import endpoints from Postman collections, JSON files, and $metadata."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bcli.config._defaults import REGISTRIES_DIR
from bcli.registry._schema import EndpointMetadata


def import_from_postman(postman_file: Path) -> list[EndpointMetadata]:
    """Parse a Postman v2.1 collection into endpoint metadata.

    Extracts publisher, group, version, entity_set_name from URL paths like:
    /v2.0/{env}/api/{publisher}/{group}/{version}/companies({id})/{entitySetName}
    """
    raw = json.loads(postman_file.read_text(encoding="utf-8"))
    endpoints: dict[str, EndpointMetadata] = {}

    def _extract_from_item(item: dict, parent_desc: str = "") -> None:
        """Recursively process Postman collection items."""
        if "item" in item:
            desc = item.get("description", parent_desc)
            for child in item["item"]:
                _extract_from_item(child, desc)
            return

        request = item.get("request")
        if not request:
            return

        method = request.get("method", "GET")
        url = request.get("url", {})

        path_segments: list[str]
        if isinstance(url, str):
            path_segments = url.split("/")
        else:
            path_segments = url.get("path", [])

        parsed = _parse_path_segments(path_segments)
        if not parsed:
            return

        publisher, group, version, entity_set_name = parsed
        key = entity_set_name.lower()

        if key not in endpoints:
            # Extract metadata from parent folder description
            source_table = ""
            description = ""
            if parent_desc:
                table_match = re.search(r"\*\*Source Table:\*\*\s*(.+)", parent_desc)
                if table_match:
                    source_table = table_match.group(1).strip().strip('"')
                desc_lines = parent_desc.split("\n")
                if desc_lines:
                    description = desc_lines[0].strip()

            endpoints[key] = EndpointMetadata(
                entity_set_name=entity_set_name,
                entity_name=_singularize(entity_set_name),
                api_publisher=publisher,
                api_group=group,
                api_version=version,
                description=description,
                source_table=source_table,
                supports=[method],
                key_field="systemId",
            )
        else:
            existing = endpoints[key]
            if method not in existing.supports:
                existing.supports.append(method)

    def _parse_path_segments(segments: list[str]) -> tuple[str, str, str, str] | None:
        """Extract (publisher, group, version, entity_set_name) from URL path segments."""
        # Look for pattern: "api", publisher, group, version, "companies(...)", entity
        try:
            api_idx = None
            for i, seg in enumerate(segments):
                if seg == "api":
                    api_idx = i
                    break

            if api_idx is None:
                return None

            # Standard v2.0: api/v2.0/companies(...)/entity
            # Custom: api/{publisher}/{group}/{version}/companies(...)/entity
            remaining = segments[api_idx + 1 :]

            if not remaining:
                return None

            # Check if this is standard v2.0
            if remaining[0].startswith("v") and remaining[0][1:].replace(".", "").isdigit():
                # Standard API — skip these, we have them built-in
                return None

            if len(remaining) < 4:
                return None

            publisher = remaining[0]
            group = remaining[1]
            version = remaining[2]

            # Skip template variables
            if publisher.startswith("{{"):
                return None

            # Find entity after companies(...)
            for i, seg in enumerate(remaining[3:], start=3):
                if seg.startswith("companies"):
                    if i + 1 < len(remaining):
                        entity = remaining[i + 1]
                        # Strip any ({{id}}) suffix
                        entity = re.sub(r"\(.*\)$", "", entity)
                        if entity and not entity.startswith("$"):
                            return (publisher, group, version, entity)
                    break

            return None
        except (IndexError, ValueError):
            return None

    # Process all items
    items = raw.get("item", [])
    for item in items:
        _extract_from_item(item)

    return sorted(endpoints.values(), key=lambda e: e.entity_set_name)


def import_from_json(json_file: Path) -> list[EndpointMetadata]:
    """Import endpoints from a raw JSON registry file.

    Supports two formats:
    1. bcli format: {"endpoints": [...]}
    2. bcmcp format: {"finance": [...], "technical": [...], ...}
    """
    raw = json.loads(json_file.read_text(encoding="utf-8"))
    endpoints: list[EndpointMetadata] = []

    # bcli format
    if "endpoints" in raw:
        for entry in raw["endpoints"]:
            endpoints.append(EndpointMetadata.model_validate(entry))
        return endpoints

    # bcmcp format (grouped by api_group)
    for group_name, items in raw.items():
        if not isinstance(items, list):
            continue
        for entry in items:
            meta = EndpointMetadata(
                entity_set_name=entry.get("entity_set_name", ""),
                entity_name=entry.get("entity_name", ""),
                api_publisher=entry.get("api_publisher", ""),
                api_group=entry.get("api_group", group_name),
                api_version=entry.get("api_version", ""),
                description=entry.get("description", ""),
                source_table=entry.get("source_table", ""),
                page_number=entry.get("page_number", ""),
                key_field=entry.get("odata_key_fields", "systemId"),
                editable=entry.get("editable", "false").lower() == "true",
                supports=["GET"] if entry.get("data_access_intent") == "ReadOnly" else ["GET", "POST", "PATCH", "DELETE"],
            )
            if meta.entity_set_name:
                endpoints.append(meta)

    return sorted(endpoints, key=lambda e: e.entity_set_name)


def save_custom_registry(
    profile_name: str,
    endpoints: list[EndpointMetadata],
    source: str = "import",
) -> Path:
    """Save imported endpoints as a custom registry for a profile."""
    REGISTRIES_DIR.mkdir(parents=True, exist_ok=True)
    registry_file = REGISTRIES_DIR / f"{profile_name}.json"

    data = {
        "source": source,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "endpoint_count": len(endpoints),
        "endpoints": [ep.model_dump(exclude_none=True) for ep in endpoints],
    }

    registry_file.write_text(json.dumps(data, indent=2))
    return registry_file


async def import_from_metadata(
    transport,
    environment: str,
    publisher: str,
    group: str,
    version: str,
) -> list[EndpointMetadata]:
    """Discover custom API endpoints from the live BC $metadata endpoint.

    Parses the OData XML $metadata to extract EntitySet names.
    """
    from bcli._url import build_metadata_url

    url = build_metadata_url(
        environment=environment,
        publisher=publisher,
        group=group,
        version=version,
    )

    # $metadata returns XML, not JSON — use raw GET
    import httpx

    auth_headers = await transport._inject_auth()
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.get(url, headers=auth_headers)
        response.raise_for_status()
        xml_text = response.text

    # Parse EntitySet elements from the EDMX XML
    endpoints: list[EndpointMetadata] = []
    # Pattern: <EntitySet Name="entitySetName" EntityType="...entityName"/>
    entity_set_pattern = re.compile(
        r'<EntitySet\s+Name="([^"]+)"\s+EntityType="[^"]*\.(\w+)"',
    )

    for match in entity_set_pattern.finditer(xml_text):
        entity_set_name = match.group(1)
        entity_type = match.group(2)

        # Skip internal/system entity sets
        if entity_set_name.startswith("$"):
            continue

        endpoints.append(EndpointMetadata(
            entity_set_name=entity_set_name,
            entity_name=entity_type,
            api_publisher=publisher,
            api_group=group,
            api_version=version,
            supports=["GET"],  # Conservative default — metadata doesn't always tell us
            key_field="systemId",
        ))

    return sorted(endpoints, key=lambda e: e.entity_set_name)


def _singularize(name: str) -> str:
    """Naive singularization for entity names."""
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name
