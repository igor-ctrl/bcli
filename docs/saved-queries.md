# Saved queries (`bcli q`)

Saved queries hide OData syntax behind named, parametrised aliases. They let
non-developer users (operations, finance, support) run their daily questions
without remembering field names, operators, or escape rules — and without
giving them a free-form CLI that could hit anything in the system.

```bash
# Look up a customer by name (no OData required)
bcli q customer-by-name name=Fabrikam

# List the saved queries available for the active profile
bcli q

# Inspect what a query resolves to without executing it
bcli q customer-by-name name=Fabrikam --show
```

## Where they live

Each profile has its own queries file:

```
~/.config/bcli/queries/<profile>.yaml
```

If the file doesn't exist, `bcli q` prints a starter example and exits.
Create the file by hand, or hand-edit it with `$EDITOR`.

## YAML schema

```yaml
queries:
  customer-by-name:
    description: Look up a customer by display name
    endpoint: customers
    params:
      name:
        required: true
    filter: "displayName eq '${{ params.name }}'"
    select: "number,displayName,email,phoneNumber"
    orderby: "displayName asc"
    top: 25

  open-invoices-by-customer:
    description: Outstanding invoices for a customer
    endpoint: customerSalesInvoices
    params:
      customer-id:
        required: true
      limit:
        default: 50
    filter: "customerNumber eq '${{ params.customer-id }}' and status eq 'Open'"
    orderby: "dueDate asc"
    top: "${{ params.limit }}"
```

### Per-query fields

| Field          | Type     | Notes                                                    |
|----------------|----------|----------------------------------------------------------|
| `description`  | string   | Shown in `bcli q` listing.                               |
| `endpoint`     | string   | Required. Entity-set name (resolved through the registry).|
| `params`       | mapping  | Optional. Each key declares a parameter; `required: true`/`default:` are honoured. |
| `filter`       | string   | OData `$filter`. Supports `${{ params.X }}` substitution. |
| `select`       | string   | Comma-separated field list.                              |
| `expand`       | string   | Comma-separated navigation properties.                   |
| `orderby`      | string   | OData `$orderby`.                                        |
| `top` / `skip` | int      | Pagination bounds.                                       |
| `all`          | bool     | If `true`, follows pagination to gather all records.     |

Anything else in `${{ ... }}` references the same template engine `bcli batch`
uses, so `${{ params.X }}` works identically.

## Using saved queries with scoped profiles

Saved queries pair well with `bcli config init --scoped` (see
`docs/configuration.md`). Together they give a non-developer user:

* a profile that can only see the endpoints they imported,
* a curated list of "questions they're allowed to ask",
* device-code login (no client secret to manage).

A typical setup for an operations team:

```bash
# 1. Admin creates the scoped profile and imports their endpoints
bcli config init --profile ops --scoped \
    --category warehouse \
    --import warehouse.postman_collection.json

# 2. Admin authors ~/.config/bcli/queries/ops.yaml with 5–10 daily questions

# 3. End user runs queries without touching OData
bcli auth login --profile ops              # one-time browser sign-in
bcli q --profile ops                       # see what's available
bcli q --profile ops items-low-stock min=10
```

## Useful flags

* `--show` — print the resolved request without executing it. Useful when
  reviewing what a saved query will actually send.
* `--format` — override the active profile's output format (`json`,
  `markdown`, `csv`, `ndjson`, `table`).
* `--dry-run` (global) — skips execution after resolving.
