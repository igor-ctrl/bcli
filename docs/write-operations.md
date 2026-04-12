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

Preview write operations without executing:

```bash
bcli --dry-run post customers --data '{"displayName": "Test"}'
# --dry-run: would POST to customers
# {"displayName": "Test"}
```

## Custom API Routes

For custom endpoints not in the registry:

```bash
bcli post engineUtilizations --publisher acme --group technical --version v1.5 \
  --data '{"esn": "ESN-123456", "period": "2026-03", "flightHours": 350.5}'
```
