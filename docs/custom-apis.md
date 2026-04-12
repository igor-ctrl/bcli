# Custom APIs

bcli ships with 79 standard Microsoft BC v2.0 entities. If you have custom API pages built in AL, you can import them so bcli auto-resolves their routes.

## Import from Postman Collection

If you have a Postman collection for your custom APIs:

```bash
bcli registry import --from-postman ./my_collection.json
```

The importer parses each request's URL to extract the API publisher, group, version, and entity set name. It also reads folder descriptions for source table metadata.

**Example output:**
```
✓ Imported 92 custom endpoints
  beautech/finance/v1.5: 46 endpoints
  beautech/standard/v1.0: 23 endpoints
  beautech/technical/v1.5: 23 endpoints
```

### Postman URL Format

The importer expects URLs following BC's custom API pattern:

```
https://api.businesscentral.dynamics.com/v2.0/{env}/api/{publisher}/{group}/{version}/companies({id})/{entity}
```

## Import from JSON

If you have endpoint metadata in JSON format:

```bash
bcli registry import --from-json ./endpoints.json
```

### Supported JSON Formats

**bcli native format:**
```json
{
  "endpoints": [
    {
      "entity_set_name": "engineOverviews",
      "entity_name": "engineOverview",
      "api_publisher": "mycompany",
      "api_group": "technical",
      "api_version": "v1.5",
      "description": "Engine overview data",
      "supports": ["GET"],
      "key_field": "systemId"
    }
  ]
}
```

**Grouped format (by API group):**
```json
{
  "finance": [
    {
      "entity_set_name": "vendors",
      "entity_name": "vendor",
      "api_publisher": "mycompany",
      "api_group": "finance",
      "api_version": "v1.5",
      "data_access_intent": "ReadOnly",
      "source_table": "Vendor"
    }
  ],
  "technical": [...]
}
```

## Import from $metadata (Live Discovery)

Query your BC environment's OData `$metadata` endpoint to discover custom APIs automatically:

```bash
bcli registry import --from-metadata
```

This requires `api_publisher`, `api_group`, and `api_version` to be set in your profile:

```bash
bcli config set profiles.default.api_publisher mycompany
bcli config set profiles.default.api_group integration
bcli config set profiles.default.api_version v1.0
bcli registry import --from-metadata
```

## Registry Management

### List Imported Registries

```bash
bcli registry list
#   production: 92 endpoints (source: postman, imported: 2026-04-12T...)
#   sandbox: 93 endpoints (source: json, imported: 2026-04-12T...)
```

### Per-Profile Registries

Registries are scoped to profiles. Import for a specific profile:

```bash
bcli registry import --from-postman ./collection.json --profile production
bcli registry import --from-postman ./collection.json --profile sandbox
```

Registries are stored at `~/.config/bcapi/registries/<profile-name>.json`.

## How Route Resolution Works

When you run `bcli get someEntity`:

1. **Custom registry** — Checks `~/.config/bcapi/registries/<profile>.json` first. If found, uses the entity's `api_publisher/api_group/api_version` to build the URL.
2. **Standard v2.0** — Falls back to the built-in standard registry. Routes to `/api/v2.0/`.
3. **Not found** — Shows an error with fuzzy search suggestions and hints to import a registry.

This means `bcli get customers` (standard) and `bcli get engineOverviews` (custom) work the same way — you never construct URLs.

## Discover Endpoints

```bash
# List all endpoints (standard + custom)
bcli endpoint list

# Only custom endpoints
bcli endpoint list --custom

# Only standard v2.0
bcli endpoint list --standard

# Filter by category
bcli endpoint list --category sales

# Search by name or description
bcli endpoint search engine

# Full details for one endpoint
bcli endpoint info engineOverviews
```

## Test a Custom Endpoint

After importing, verify an endpoint works:

```bash
bcli test endpoint engineOverviews
# ✓ engineOverviews: returned 1 record(s)
#   Fields: systemId, esn, engineModel, status, ...
```
