# Write Operations

## POST (Create)

Create a new record:

```bash
# Inline JSON
bcli post customers --data '{"displayName": "New Customer", "email": "new@example.com"}'

# From file
bcli post customers --data @customer.json
```

The `@` prefix reads JSON from a file. The response shows the created record.

The `@` is required — a bare path (`--data customer.json`, no `@`) is parsed
as inline JSON and rejected, it isn't silently treated as a file. If your
shell strips quotes from inline JSON (PowerShell does this routinely), pass
`--data @customer.json` instead of fighting the quoting. Either way, a
malformed `--data` value fails with a usage error naming the problem, not a
Python traceback.

## PATCH (Update)

Update an existing record by ID:

```bash
bcli patch customers "a1b2c3d4-..." --data '{"email": "updated@example.com"}'

# From file
bcli patch customers "a1b2c3d4-..." --data @update.json
```

### ETag (Optimistic Concurrency)

BC uses ETags to prevent conflicting updates. By default, bcli sends `If-Match: *` (overwrite regardless). To enforce concurrency:

```bash
# First, get the record and note its @odata.etag
bcli -f raw get customers "a1b2c3d4-..."

# Then patch with the specific ETag
bcli patch customers "a1b2c3d4-..." --data '{"email": "new@email.com"}' --etag 'W/"ABC123"'
```

## DELETE

Delete a record by ID:

```bash
bcli delete customers "a1b2c3d4-..."
```

ETags work the same way as PATCH:

```bash
bcli delete customers "a1b2c3d4-..." --etag 'W/"ABC123"'
```

## Dry Run

Preview write operations without executing. Works on `post`, `patch`, `delete`,
and `attach upload`. The output adapts to `--format`:

**Human format (default):** rich panel on stderr with the resolved URL, profile
context, and the request body.

```bash
bcli --dry-run post customers --data '{"displayName": "Test"}'
# --dry-run: would POST customers
#   URL:        https://api.businesscentral.dynamics.com/.../api/v2.0/companies(<id>)/customers
#   Profile:    dev
#   Env:        Sandbox
#   Company:    <company-id>
# {
#   "displayName": "Test"
# }
```

**Machine format (`-f json` / `-f ndjson` / `-f raw`):** a single JSON envelope
on stdout that an agent can parse before deciding whether to proceed:

```bash
bcli --dry-run -f json post customers --data '{"displayName": "Test"}'
```

```json
{
  "dry_run": true,
  "method": "POST",
  "endpoint": "customers",
  "resolved_url": "https://api.businesscentral.dynamics.com/.../customers",
  "profile": "dev",
  "environment": "Sandbox",
  "company_id": "<company-id>",
  "body": {"displayName": "Test"}
}
```

`PATCH` and `DELETE` envelopes also include `record_id`. `attach upload` adds
`file_path`, `byte_size`, `parent_type`, and `parent_id`. The envelope shape is
stable — agents can rely on the field names.

When the audit log is enabled (see [Audit Log](configuration.md#audit-log)), each
dry-run is recorded with `outcome: "dry_run"` so the paper trail captures intent
even when no HTTP call fires.

## Custom API Routes

For custom endpoints not in the registry:

```bash
bcli post warehouseEntries --publisher contoso --group logistics --version v2.0 \
  --data '{"itemNumber": "1000", "quantity": 50, "locationCode": "WEST"}'
```
