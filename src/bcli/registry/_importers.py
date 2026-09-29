"""Import endpoints from registry files (JSON/YAML), Postman collections, and $metadata."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from bcli.config._defaults import REGISTRIES_DIR
from bcli.registry._schema import CautionLevel, EndpointMetadata

# Verbs in BC entity-set names that mutate posted/closed records.
# Hits any of these (case-insensitive, camelCase or all-caps token) →
# ``caution: high``. Generic BC terms only — no domain-specific vocabulary.
_DANGEROUS_VERBS = frozenset({
    "post",
    "release",
    "cancel",
    "void",
    "reverse",
    "apply",
    "unapply",
})

# Splits a name into tokens by camelCase boundaries:
#   "salesInvoicePost"  -> ["sales", "Invoice", "Post"]
#   "PaymentReverse"    -> ["Payment", "Reverse"]
#   "salesinvoiceCANCEL"-> ["salesinvoice", "CANCEL"]
# Falls back to a single all-caps chunk when there are no boundaries
# (e.g. ``SALESINVOICEPOST``); we substring-check those separately.
_CAMEL_SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")


def _infer_caution(entity_set_name: str) -> CautionLevel:
    """Heuristic ``caution`` level from an entity-set name.

    Returns ``"high"`` if the name contains any of ``_DANGEROUS_VERBS`` as a
    discrete token (camelCase boundary aware) or as a prefix/suffix of an
    all-uppercase string. Returns ``"low"`` otherwise. Never returns
    ``"medium"`` — that level is reserved for explicit setting by importers
    or curators who know the endpoint's actual semantics.
    """
    if not entity_set_name:
        return "low"

    if entity_set_name.isupper():
        lowered = entity_set_name.lower()
        for verb in _DANGEROUS_VERBS:
            if lowered.startswith(verb) or lowered.endswith(verb):
                return "high"
        return "low"

    for token in _CAMEL_SPLIT.findall(entity_set_name):
        if token.lower() in _DANGEROUS_VERBS:
            return "high"
    return "low"


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
                category=group,
                description=description,
                source_table=source_table,
                supports=[method],
                key_field="systemId",
                caution=_infer_caution(entity_set_name),
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


_ROUTE_KEYS = (
    ("api_publisher", "publisher"),
    ("api_group", "group"),
    ("api_version", "version"),
)


def import_from_file(path: Path) -> list[EndpointMetadata]:
    """Import endpoints from a JSON or YAML registry file.

    Supports two layouts:

    1. bcli format — ``{"endpoints": [...]}``. Route keys
       (``publisher``/``group``/``version``, or their ``api_*`` spellings)
       may be set once at the top level and are inherited by every entry,
       and an entry may be just an entity-set name::

           publisher: contoso
           group: integration
           version: v1.0
           endpoints:
             - shipmentTrackings
             - entity_set_name: carrierRates
               supports: [GET, POST]

    2. Grouped format — ``{"<api_group>": [...], ...}``.

    Raises ``ValueError`` if an entry names only part of a custom route.
    """
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".yaml", ".yml"):
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")

    if "endpoints" in raw:
        return parse_endpoint_list(raw)
    return _import_grouped(raw)


def import_from_json(json_file: Path) -> list[EndpointMetadata]:
    """Import endpoints from a registry file. See :func:`import_from_file`."""
    return import_from_file(json_file)


def parse_endpoint_list(raw: dict) -> list[EndpointMetadata]:
    defaults: dict[str, str] = {}
    for canonical, short in _ROUTE_KEYS:
        value = raw.get(canonical) or raw.get(short)
        if value:
            defaults[canonical] = value

    endpoints: list[EndpointMetadata] = []
    for item in raw["endpoints"] or []:
        entry = {"entity_set_name": item} if isinstance(item, str) else dict(item)
        for canonical, short in _ROUTE_KEYS:
            if short in entry and canonical not in entry:
                entry[canonical] = entry.pop(short)
        entry = {**defaults, **entry}
        name = entry.get("entity_set_name", "")
        if not name:
            raise ValueError(f"endpoint entry is missing entity_set_name: {item!r}")

        route = [entry.get(canonical) for canonical, _ in _ROUTE_KEYS]
        if any(route) and not all(route):
            raise ValueError(
                f"endpoint '{name}' needs all of publisher, group and version"
                " (set them on the entry or once at the top of the file)"
            )
        if all(route):
            entry.setdefault("entity_name", _singularize(name))
            entry.setdefault("key_field", "systemId")
            entry.setdefault("category", entry["api_group"])
        entry.setdefault("caution", _infer_caution(name))
        endpoints.append(EndpointMetadata.model_validate(entry))
    return endpoints


def _import_grouped(raw: dict) -> list[EndpointMetadata]:
    endpoints: list[EndpointMetadata] = []
    for group_name, items in raw.items():
        if not isinstance(items, list):
            continue
        for entry in items:
            api_group = entry.get("api_group", group_name)
            entity_set_name = entry.get("entity_set_name", "")
            meta = EndpointMetadata(
                entity_set_name=entity_set_name,
                entity_name=entry.get("entity_name", ""),
                api_publisher=entry.get("api_publisher", ""),
                api_group=api_group,
                api_version=entry.get("api_version", ""),
                category=entry.get("category", api_group),
                description=entry.get("description", ""),
                source_table=entry.get("source_table", ""),
                page_number=entry.get("page_number", ""),
                key_field=entry.get("odata_key_fields", "systemId"),
                editable=entry.get("editable", "false").lower() == "true",
                supports=["GET"] if entry.get("data_access_intent") == "ReadOnly" else ["GET", "POST", "PATCH", "DELETE"],
                caution=entry.get("caution") or _infer_caution(entity_set_name),
            )
            if meta.entity_set_name:
                endpoints.append(meta)

    return sorted(endpoints, key=lambda e: e.entity_set_name)


def save_custom_registry(
    profile_name: str,
    endpoints: list[EndpointMetadata],
    source: str = "import",
    *,
    replace: bool = False,
) -> Path:
    """Save imported endpoints into a profile's custom registry.

    Merges by entity-set name (case-insensitive) with what is already
    there, so importing a second API group keeps the first. With
    ``replace=True`` previously imported endpoints are dropped, but
    entries installed by a pack (``source_pack``) are kept — the pack's
    ledger still owns them.
    """
    REGISTRIES_DIR.mkdir(parents=True, exist_ok=True)
    registry_file = REGISTRIES_DIR / f"{profile_name}.json"

    merged: dict[str, dict] = {}
    if registry_file.is_file():
        existing = json.loads(registry_file.read_text(encoding="utf-8"))
        for entry in existing.get("endpoints") or []:
            if not isinstance(entry, dict) or not entry.get("entity_set_name"):
                continue
            if replace and not entry.get("source_pack"):
                continue
            merged[entry["entity_set_name"].lower()] = entry
    for ep in endpoints:
        merged[ep.entity_set_name.lower()] = ep.model_dump(exclude_none=True)

    data = {
        "source": source,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "endpoint_count": len(merged),
        "endpoints": sorted(merged.values(), key=lambda e: e["entity_set_name"].lower()),
    }

    registry_file.write_text(json.dumps(data, indent=2))
    return registry_file


def export_custom_registry(profile_name: str) -> dict:
    """Return a profile's custom endpoints in the portable bcli format.

    The result can be written to a file and imported on another machine
    with ``bcli registry import --from-file``.
    """
    registry_file = REGISTRIES_DIR / f"{profile_name}.json"
    if not registry_file.is_file():
        return {"endpoints": []}
    raw = json.loads(registry_file.read_text(encoding="utf-8"))
    endpoints = []
    for entry in raw.get("endpoints") or []:
        if not isinstance(entry, dict):
            continue
        meta = EndpointMetadata.model_validate(entry)
        if not meta.is_custom:
            continue
        dumped = meta.model_dump(exclude_none=True)
        dumped.pop("source_pack", None)
        dumped.pop("pack_version", None)
        endpoints.append(dumped)
    return {"endpoints": endpoints}


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

    return _parse_metadata_xml(xml_text, publisher=publisher, group=group, version=version)


def _parse_metadata_xml(
    xml_text: str,
    *,
    publisher: str,
    group: str,
    version: str,
) -> list[EndpointMetadata]:
    """Extract EntitySets and their Property names from EDMX XML.

    Pulled out as a pure function so unit tests can hit it without spinning
    up an HTTP transport.
    """
    # Parse EntitySet elements from the EDMX XML
    endpoints: list[EndpointMetadata] = []
    # Pattern: <EntitySet Name="entitySetName" EntityType="...entityName"/>
    entity_set_pattern = re.compile(
        r'<EntitySet\s+Name="([^"]+)"\s+EntityType="[^"]*\.(\w+)"',
    )

    fields_by_type = _parse_entity_type_properties(xml_text)

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
            category=group,
            supports=["GET"],  # Conservative default — metadata doesn't always tell us
            key_field="systemId",
            field_names=fields_by_type.get(entity_type, []),
            caution=_infer_caution(entity_set_name),
        ))

    return sorted(endpoints, key=lambda e: e.entity_set_name)


def _parse_entity_type_properties(xml_text: str) -> dict[str, list[str]]:
    """Map EntityType name → declared Property names from EDMX XML.

    Looks for blocks like:
        <EntityType Name="customer">
          <Property Name="number" .../>
          <Property Name="displayName" .../>
        </EntityType>
    Returns {entity_type_name: [field, ...]}.
    """
    result: dict[str, list[str]] = {}
    block_pattern = re.compile(
        r'<EntityType\s+Name="([^"]+)"[^>]*>(.*?)</EntityType>',
        re.DOTALL,
    )
    prop_pattern = re.compile(r'<Property\s+Name="([^"]+)"')
    for block in block_pattern.finditer(xml_text):
        entity_type = block.group(1)
        body = block.group(2)
        result[entity_type] = [m.group(1) for m in prop_pattern.finditer(body)]
    return result


def update_endpoint_fields(
    profile_name: str,
    entity_set_name: str,
    field_names: list[str],
) -> bool:
    """Persist a learned field list onto an existing custom-registry entry.

    Used after `bcli endpoint fields` (or any sample-fetch path) discovers
    actual field names from a live record. Returns True if the registry was
    updated, False if the endpoint isn't in the custom registry.
    """
    registry_file = REGISTRIES_DIR / f"{profile_name}.json"
    if not registry_file.is_file():
        return False
    raw = json.loads(registry_file.read_text(encoding="utf-8"))
    target = entity_set_name.lower()
    updated = False
    for entry in raw.get("endpoints", []):
        if entry.get("entity_set_name", "").lower() == target:
            entry["field_names"] = sorted(set(field_names))
            updated = True
            break
    if updated:
        registry_file.write_text(json.dumps(raw, indent=2))
    return updated


def _singularize(name: str) -> str:
    """Naive singularization for entity names."""
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name
