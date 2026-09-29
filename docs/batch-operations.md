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
name: "Monthly Sales Invoice for Adatum"
steps:
  - name: customer
    action: get
    endpoint: customers
    params:
      filter: "number eq '10000'"
      select: "id,number,displayName"
      top: 1

  - name: invoice
    action: post
    endpoint: salesInvoices
    data:
      customerNumber: "${{ steps.customer.0.number }}"
      invoiceDate: "2026-03-31"
      externalDocumentNumber: "PO-2026-03"

  - action: post
    endpoint: salesInvoiceLines
    data:
      documentId: "${{ steps.invoice.id }}"
      lineType: "Item"
      lineObjectNumber: "1896-S"
      quantity: 2

  - action: post
    endpoint: salesInvoiceLines
    data:
      documentId: "${{ steps.invoice.id }}"
      lineType: "Item"
      lineObjectNumber: "1900-S"
      quantity: 4
```

The GET result feeds the invoice header, and the header's `id` feeds each
line. See [Step Chaining](#step-chaining) for the `${{ steps.<name>... }}`
syntax.

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
  endpoint: salesQuotes
  id: "e5f6a7b8-..."
```

## Dry Run

Preview all steps without executing:

```bash
bcli batch run operations.yaml --dry-run
```

Output:
```
Batch: Monthly Sales Invoice for Adatum
4 step(s)

  Step 1: GET customers (customer)
    Params: {'filter': "number eq '10000'", 'select': 'id,number,displayName', 'top': 1}
  Step 2: POST salesInvoices (invoice)
    Data: {"customerNumber": "${{ steps.customer.0.number }}", ...}
  Step 3: POST salesInvoiceLines
    Data: {"documentId": "${{ steps.invoice.id }}", "lineType": "Item", "lineObjectNumber": "1896-S", ...}
  Step 4: POST salesInvoiceLines
    Data: {"documentId": "${{ steps.invoice.id }}", "lineType": "Item", "lineObjectNumber": "1900-S", ...}

--dry-run: 4 step(s) would execute.
```

## Error Handling

If a step fails, the error is reported and subsequent steps continue:

```
  Step 1: GET customers... ✓ 1 record(s)
  Step 2: POST salesInvoices... ✓ created
  Step 3: POST salesInvoiceLines... ✓ created
  Step 4: POST salesInvoiceLines... ✗ HTTP 400: Blocked must be equal to 'No'  in Item: No.=1900-S. Current value is 'Yes'.

✓ Batch complete: 3/4 steps succeeded
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
  - name: invoices
    action: get
    endpoint: purchaseInvoices
    params:
      filter: "vendorNumber eq '${{ params.vendor }}' and postingDate ge ${{ params.month }}-01"
      select: "number,vendorNumber,totalAmountIncludingTax,currencyCode,vendorInvoiceNumber"
      top: 500

  - name: aging
    action: get
    endpoint: agedAccountsPayables
    params:
      filter: "vendorNumber eq '${{ params.vendor }}'"
      select: "vendorNumber,name,currencyCode,balanceDue,currentAmount,period1Amount"
```

Run it:

```bash
bcli batch run month-end-recon.yaml --set vendor=V00011 --set month=2026-03 -o recon.json
# Or from a params file:
bcli batch run month-end-recon.yaml --params march.yaml -o recon.json
```

`--dry-run` prints each step with references resolved, so you can verify substitution before executing.

## Step Chaining

Steps can reference results from previous steps via `${{ steps.<step_name>.<path> }}`. Give a step a `name:` to make its results addressable. Step names must use word characters only (`[A-Za-z0-9_]`) — no hyphens.

Path traversal rules:

- **GET** returns a list → index with integers: `${{ steps.find_vendor.0.number }}`
- **POST / PATCH** returns a single record → access fields directly: `${{ steps.create_header.no }}`
- Nested fields use dots: `${{ steps.find_vendor.0.address.city }}`
- List length: `${{ steps.find_vendor.length }}`

```yaml
name: "Chain: find vendor, then list their invoices"
steps:
  - name: find_vendor
    action: get
    endpoint: vendors
    params:
      filter: "displayName eq 'Fabrikam'"
      select: "number"
      top: 1

  - name: invoices
    action: get
    endpoint: purchaseInvoices
    params:
      filter: "vendorNumber eq '${{ steps.find_vendor.0.number }}'"
      top: 50
```

Step references are resolved at execution time — when a step fails, downstream steps that reference its data will fail to resolve and will be skipped with a clear error.

> **Note:** `id:` on a step is reserved for the *record id* used by `patch` and `delete` actions. It is **not** a step identifier. Use `name:` to identify steps for chaining.

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
