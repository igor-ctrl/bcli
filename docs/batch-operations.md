# Batch Operations

Execute sequences of API calls from YAML files. Batch files can be simple linear scripts or parameterized workflows with step-to-step value references.

## Usage

```bash
bcli batch run operations.yaml                          # Run all steps
bcli batch run operations.yaml --dry-run                # Preview without executing
bcli batch run operations.yaml -o results.json          # Save full result bundle
bcli batch run operations.yaml -f table                 # Print each step's rows inline
bcli batch run workflow.yaml --set vendor=V00011 --set month=2026-03
bcli batch run workflow.yaml --params month-end.yaml
```

| Flag | Short | Purpose |
|------|-------|---------|
| `--dry-run` | | Resolve references and parameters, print resolved requests, do not execute |
| `--output <path>` | `-o` | Save full results to a JSON file — one entry per step with status, data, and metadata |
| `--format <fmt>` | `-f` | Print each step's returned data inline (`table`, `json`, `csv`, `ndjson`) |
| `--set key=value` | | Set a workflow parameter (repeatable). Values auto-typed via YAML scalar rules (`"4500"` → int, `"true"` → bool, `"V00011"` → str) |
| `--params <file>` | | Load workflow parameters from a YAML mapping file |

## Batch File Format

```yaml
name: "Monthly Engine Utilization Upload"
steps:
  - action: get
    endpoint: engineOverviews
    params:
      filter: "engineModel eq 'CF34-10E'"
      top: 5

  - action: post
    endpoint: engineUtilizations
    data:
      esn: "ESN-123456"
      period: "2026-03"
      flightHours: 350.5
      flightCycles: 210

  - action: post
    endpoint: engineUtilizations
    data:
      esn: "ESN-789012"
      period: "2026-03"
      flightHours: 280.0
      flightCycles: 175

  - action: patch
    endpoint: engineCards
    id: "a1b2c3d4-..."
    data:
      status: "Available"

  - action: delete
    endpoint: tempRecords
    id: "e5f6a7b8-..."
```

## Step Actions

### GET

```yaml
- action: get
  endpoint: customers
  params:
    filter: "city eq 'Chicago'"
    select: "displayName,email"
    top: 10
    orderby: "displayName asc"
```

### POST

```yaml
- action: post
  endpoint: customers
  data:
    displayName: "New Customer"
    email: "new@example.com"
```

### PATCH

```yaml
- action: patch
  endpoint: customers
  id: "a1b2c3d4-..."
  data:
    email: "updated@example.com"
  etag: "*"  # optional, defaults to "*"
```

### DELETE

```yaml
- action: delete
  endpoint: tempRecords
  id: "e5f6a7b8-..."
```

## Dry Run

Preview all steps without executing:

```bash
bcli batch run operations.yaml --dry-run
```

Output:
```
Batch: Monthly Engine Utilization Upload
3 step(s)

  Step 1: GET engineOverviews
    Params: {'filter': "engineModel eq 'CF34-10E'", 'top': 5}
  Step 2: POST engineUtilizations
    Data: {"esn": "ESN-123456", ...}
  Step 3: POST engineUtilizations
    Data: {"esn": "ESN-789012", ...}

--dry-run: 3 step(s) would execute.
```

## Error Handling

If a step fails, the error is reported and subsequent steps continue:

```
  Step 1: GET engineOverviews... ✓ 5 record(s)
  Step 2: POST engineUtilizations... ✓ created
  Step 3: POST engineUtilizations... ✗ HTTP 400: Duplicate record

✓ Batch complete: 2/3 steps succeeded
```

## Parameterized Workflows

Batch files can declare named `params` and reference them with `${{ params.<name> }}` inside step fields. Parameters are supplied at run time via `--set key=value` (repeatable) or a `--params <file.yaml>`.

```yaml
# month-end-recon.yaml
name: "Month-End AP Reconciliation"
params:
  vendor:
    required: true
  month:
    required: true
  tolerance:
    default: 0.01

steps:
  - id: invoices
    action: get
    endpoint: purchaseInvoices
    params:
      filter: "vendorNumber eq '${{ params.vendor }}' and postingDate ge ${{ params.month }}-01"
      select: "number,vendorNumber,amount,currencyCode,externalDocumentNumber"
      top: 500

  - id: ledger
    action: get
    endpoint: vendorLedgerEntries
    params:
      filter: "vendorNumber eq '${{ params.vendor }}' and postingDate ge ${{ params.month }}-01"
      select: "documentNumber,amount,currencyCode"
      top: 500
```

Run it:

```bash
bcli batch run month-end-recon.yaml --set vendor=V00011 --set month=2026-03 -o recon.json
# Or from a params file:
bcli batch run month-end-recon.yaml --params march.yaml -o recon.json
```

`--dry-run` prints each step with references resolved, so you can verify substitution before executing.

## Step Chaining

Steps can reference results from previous steps via `${{ steps.<step-id>.data }}`. Give a step an `id:` to make its results addressable.

```yaml
name: "Chain: find vendor, then list their invoices"
steps:
  - id: find-vendor
    action: get
    endpoint: vendors
    params:
      filter: "displayName eq 'Fabrikam'"
      select: "number"
      top: 1

  - id: invoices
    action: get
    endpoint: purchaseInvoices
    params:
      filter: "vendorNumber eq '${{ steps.find-vendor.data[0].number }}'"
      top: 50
```

Step references are resolved at execution time — when a step fails, downstream steps that reference its data will fail to resolve and will be skipped with a clear error.

## Result Capture

Pass `--output` / `-o` to save the full result bundle as JSON:

```bash
bcli batch run workflow.yaml -o results.json
```

The bundle contains one entry per step with the step id, action, endpoint, resolved params, returned data, record counts, and timing. Use `jq` or any JSON consumer (Claude, Python, Airflow) to work with it downstream.

Pass `--format` / `-f` to additionally print each step's data inline in the chosen format:

```bash
bcli batch run workflow.yaml -f table    # Each step's rows as a table
bcli batch run workflow.yaml -f ndjson   # Streaming-friendly
```
