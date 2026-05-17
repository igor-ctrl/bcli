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
| `description`  | string   | Shown in `bcli q` listing **and** the generated slash command's frontmatter. |
| `categories`   | list[str] | Optional. Used by `bcli skill install` to group commands in the generated `SKILL.md` index. Falls back to `["unsorted"]`. |
| `args`         | list[obj] | Optional. Explicit positional ordering for the generated slash command. If omitted, inferred from `params:` keys (required first, optional second). See *Slash-command projection* below. |
| `endpoint`     | string   | Required. Entity-set name (resolved through the registry).|
| `params`       | mapping  | Optional. Each key declares a parameter; see *Param declarations* below. |
| `filter`       | string   | OData `$filter`. Supports `${{ params.X }}` substitution. |
| `select`       | string   | Comma-separated field list.                              |
| `expand`       | string   | Comma-separated navigation properties.                   |
| `orderby`      | string   | OData `$orderby`.                                        |
| `top` / `skip` | int      | Pagination bounds.                                       |
| `all`          | bool     | If `true`, follows pagination to gather all records.     |

Anything else in `${{ ... }}` references the same template engine `bcli batch`
uses, so `${{ params.X }}` works identically.

### Param declarations

Each entry under `params:` is a small schema. The full set of keys:

| Key        | Notes                                                         |
|------------|---------------------------------------------------------------|
| `required` | If `true`, the user must pass `key=value` on the command line. |
| `default`  | Value used when the user doesn't pass one.                    |
| `type`     | One of `string`, `integer`, `number`, `boolean`. Drives coercion *and* per-type validation. Defaults to "no coercion" (the value is passed through as-is). |
| `pattern`  | Regex (full-match) for string params. Rejected values fail locally before any HTTP call. |
| `min` / `max` | Numeric bounds for `integer` / `number` params. |
| `enum`     | List of allowed literal values (any type).                    |

Example with all the validation knobs:

```yaml
queries:
  utilization-by-esn:
    description: Monthly utilization records for one ESN
    endpoint: engineUtilizations
    params:
      esn:
        required: true
        type: string
        pattern: "^[A-Za-z0-9-]{4,16}$"   # ESNs are alphanumeric, no spaces
      limit:
        default: 24
        type: integer
        min: 1
        max: 1000
      airline:
        required: false
        type: string
        enum: ["AIRNORTH", "QANTAS", "VIRGIN"]
    filter: "engineSerialNumber eq '${{ params.esn }}'"
    orderby: "asOfDate desc"
    top: "${{ params.limit }}"
```

### Filter-context safety

When a string-typed param is interpolated into the `filter:` field, `bcli`
applies OData v4 single-quote escaping (`'` → `''`) so a value like
`193208' or 1 eq 1--` cannot break out of the surrounding string literal.
This escape is scoped to the filter context — `select`, `orderby`, `top`,
`skip`, `all`, and `endpoint` keep raw values, since they don't sit inside
OData string literals.

For defense in depth, declare a `pattern:` (or use `type: integer`) on params
that should not contain free-form text. The structural escape protects
against the syntactic case; the type / pattern check rejects malformed input
before it reaches the wire.

> **Note on `--show` output.** A vendor name like `O'Brien` rendered with
> `--show` will appear as `'O''Brien'` in the resolved filter — that's the
> OData literal form, not a bug. BC parses the doubled quote back to a
> single quote on the server side.

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
bcli --profile ops auth login              # one-time browser sign-in
bcli --profile ops q                       # see what's available
bcli --profile ops q items-low-stock min=10
```

## Useful flags

* `--show` — print the resolved request without executing it. Useful when
  reviewing what a saved query will actually send.
* `--format` — override the active profile's output format (`json`,
  `markdown`, `csv`, `ndjson`, `table`).
* `--dry-run` (global) — skips execution after resolving.

## Slash-command projection (`bcli skill install`)

`bcli skill install` reads the saved queries for the active profile and
generates one Claude Code slash command per query at
`<target>/.claude/commands/bcli-<name>.md`, plus a top-level skill index
at `<target>/.claude/skills/bcli/SKILL.md` grouped by `categories:`.

Three optional fields on each query feed the generator:

```yaml
queries:
  utilization-by-esn:
    description: Engine utilization (cycles, hours, FSN) for an ESN
    categories: [aviation, daily-ops]
    args:
      - name: esn
        type: string
        example: "424322"
        required: true
    # existing fields below — params/filter/select/etc.
    endpoint: util_history
    params:
      esn: {required: true}
    filter: "engine_serial eq '${{ params.esn }}'"
```

* `description` — used as the slash command's frontmatter `description:`
  and listed under its category in `SKILL.md`.
* `categories` — list of strings. Each category becomes a section in
  `SKILL.md`. Queries with no categories land under `unsorted`.
* `args` — explicit positional ordering for the generated slash command
  body. Each entry: `{name, type, required, example}`. **If omitted, the
  generator derives `args:` from `params:` keys** (required first,
  optional with `default:` second, both in YAML insertion order). For
  most queries you can leave `args:` out and the projected command will
  still work; declare it explicitly when you want a different ordering
  than the params dict gives you.

The generated command body invokes `bcli q <name> arg1=$1 arg2=$2 …
--format json`, so the positional → key mapping in the slash command
matches your `args:` order.

### Idempotency and manual overrides

* Each generated file embeds a `content_hash: sha256:…` line in its
  provenance comment. Re-running `bcli skill install` on unchanged
  sources is a no-op (hash matches → file isn't rewritten).
* To protect a hand-edited slash command file from regeneration, add
  `manual: true` to its YAML frontmatter:
  ```markdown
  ---
  manual: true
  description: My customised command
  ---
  ```
  The installer skips any file whose frontmatter declares `manual: true`,
  even if a saved query by the same name exists.
* Add `--dry-run` to preview without writing; add `--target PATH` to
  point at a specific project root (defaults to CWD when it contains a
  `.claude/` directory, else `$HOME`).

### Batch templates

Batch workflow YAMLs under `~/.config/bcli/batches/<profile>/*.yaml` are
projected the same way as saved queries — one
`.claude/commands/bcli-batch-<name>.md` per file, body invoking
`bcli batch run <yaml> --set arg=$1 --format json --result-out …`.

CWD-relative batch discovery (a `batches/` directory checked into a
project repo) is a follow-up — open an issue if you need it.
