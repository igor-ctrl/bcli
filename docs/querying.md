# Querying Data

## Basic GET

```bash
# Get all records (first page)
bcli get customers

# Limit results
bcli get customers --top 10

# Get a single record by ID
bcli get customers "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

## OData Filtering

Business Central APIs use OData v4 query syntax.

### Filter

```bash
# Equality
bcli get customers --filter "displayName eq 'Fabrikam'"

# Comparison
bcli get salesInvoices --filter "totalAmountIncludingTax gt 1000"

# Contains
bcli get items --filter "contains(displayName, 'Chair')"

# Date filtering
bcli get generalLedgerEntries --filter "postingDate ge 2024-01-01"

# Combined (AND)
bcli get salesInvoices --filter "status eq 'Open' and totalAmountIncludingTax gt 500"
```

### Select Fields

```bash
bcli get customers --select displayName,email,phoneNumber
bcli get items --select number,displayName,unitPrice,inventory
```

### Expand Navigation Properties

```bash
bcli get salesOrders --expand salesOrderLines
bcli get customers --expand defaultDimensions
```

### Sort

```bash
bcli get customers --orderby "displayName asc"
bcli get salesInvoices --orderby "totalAmountIncludingTax desc"
```

### Pagination

```bash
# Skip and take
bcli get customers --skip 20 --top 10

# Get ALL records (follows @odata.nextLink automatically)
bcli get customers --all

# Include total count
bcli get customers --count --top 5
```

## Output Formats

Control output with the global `--format` (`-f`) flag:

```bash
# Rich table (default)
bcli get customers --top 5

# JSON (for scripting, piping to jq)
bcli -f json get customers --top 5

# CSV (for spreadsheets, Excel)
bcli -f csv get customers --top 5 > customers.csv

# NDJSON (newline-delimited JSON, for streaming pipelines)
bcli -f ndjson get customers --all

# Raw (includes @odata metadata fields)
bcli -f raw get customers --top 1
```

### Pipe to jq

```bash
bcli -f json -q get customers --top 100 | jq '.[] | select(.city == "Chicago") | .displayName'
```

### Export to CSV

```bash
bcli -f csv -q get items --select number,displayName,unitPrice --all > items.csv
```

## Downloading Media Streams (PDFs)

Some records carry a binary attachment — a scanned invoice, a rendered
document, an image. BC advertises those as `<field>@odata.mediaReadLink`
annotations on the record, and `--out` streams the bytes to a file instead of
printing records.

It takes two steps, because a media download addresses exactly one record:

```bash
# 1. Find the record's systemId.
bcli get incomingDocuments --filter "description eq 'March invoice'" --top 1 -f json

# 2. Download its media stream.
bcli get incomingDocuments <systemId> --out invoice.pdf
# ✓ Wrote 48,215 bytes to invoice.pdf (application/pdf, media field: content)
```

With no `--media`, the media property is auto-discovered from the record's
annotations. If the record exposes several, bcli lists them and asks you to
pick one rather than guessing:

```bash
bcli get incomingDocuments <systemId> --media attachmentContent --out invoice.pdf
```

Notes:

- An existing file is never replaced without `--overwrite`, and a missing
  parent directory is an error rather than an `mkdir`.
- The record is fetched through the normal endpoint resolution, so a profile
  with `disable_standard_api = true` refuses a media download from an
  unregistered entity exactly as it refuses a read.
- `--out` is single-record mode: it can't be combined with `--filter`,
  `--select`, `--top`, `--all` and friends. Use step 1 for those.
- For a bound action that *returns* a base64 payload, the equivalent flag is
  `bcli action ... --out` (see the command reference).

## Context Banner

By default, bcli shows the active profile, environment, and company before output:

```
[profile: production | env: Production | company: CRONUS USA]
```

Suppress it with `--quiet` (`-q`):

```bash
bcli -q get customers --top 5
```

## Verbose Mode

See resolved URLs and timing:

```bash
bcli -v get customers --top 5
# Endpoint: customers (v2.0 (standard))
# OData: {'$top': '5'}
```

## Dry Run

Preview what would execute without making any requests:

```bash
bcli --dry-run get customers --filter "city eq 'Chicago'" --top 10
# --dry-run: would execute GET, skipping.
```

## Ad-Hoc Custom API Queries

If an endpoint isn't in your registry, use explicit route flags:

```bash
bcli get myCustomEntity --publisher mycompany --group api --version v1.0 --top 5
```
