# Command Reference

## Global Options

These flags go **before** the command:

```bash
bcli [global-options] <command> [command-options]
```

| Flag | Short | Description |
|------|-------|-------------|
| `--profile <name>` | `-p` | Use a specific profile |
| `--env <name>` | `-e` | Override environment name |
| `--company <id-or-alias>` | `-c` | Override company (alias, GUID, or `all`) |
| `--format <type>` | `-f` | Output format: `table`, `json`, `csv`, `ndjson`, `raw` |
| `--verbose` | `-v` | Show resolved URLs and timing |
| `--debug` | | Show full HTTP request/response |
| `--dry-run` | | Preview without executing |
| `--quiet` | `-q` | Suppress context banner |
| `--version` | `-V` | Show version |

---

## config

### config init

Interactive setup wizard. Defaults to browser auth and discovers companies
automatically.

```bash
bcli config init
bcli config init --automation
bcli config init --headless
```

| Option | Description |
|--------|-------------|
| `--auth <method>` | `browser`, `client-credentials`, or `device-code` |
| `--automation` | Shortcut for client credentials |
| `--headless` | Shortcut for device code |
| `--scoped` | Hide standard APIs; only imported endpoints are visible |
| `--import <file>` | Import custom endpoints after profile creation |

### config show

Print resolved configuration (secrets redacted).

```bash
bcli config show
```

### config set

Set a configuration value.

```bash
bcli config set <key> <value>
```

Key format: `defaults.<field>` or `profiles.<name>.<field>`

```bash
bcli config set defaults.format json
bcli config set profiles.prod.environment Production
```

### config use

Switch the active profile.

```bash
bcli config use <profile-name>
```

---

## auth

### auth login

Authenticate and cache a token.

```bash
bcli auth login [--method <method>] [--incognito]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--method <method>` | `-m` | `browser`, `device`, or `client_credentials` (default: profile's `auth_method`) |
| `--incognito` | `-i` | Open the browser in incognito/private mode — useful for logging in as a different user |

Examples:
```bash
bcli auth login                              # uses profile's auth_method
bcli auth login --method browser             # browser OAuth (user's BC permissions, PKCE)
bcli auth login --method device              # device code flow
bcli auth login --method client_credentials  # service-to-service
```

See [Authentication](authentication.md) for method details.

### auth status

Show token cache and keychain status.

```bash
bcli auth status
```

### auth logout

Clear cached tokens for the active profile.

```bash
bcli auth logout
```

### auth store-secret

Store client secret in the OS keychain.

```bash
bcli auth store-secret
```

### auth delete-secret

Remove client secret from the OS keychain.

```bash
bcli auth delete-secret
```

---

## env

### env list

List available BC environments.

```bash
bcli env list
```

### env use

Set the default environment. Clears company selection.

```bash
bcli env use <environment-name>
```

---

## company

### company list

List all companies in the current environment.

```bash
bcli company list
```

### company use

Set the default company.

```bash
bcli company use <company-id-or-alias>
```

### company alias

Assign a nickname to a company.

```bash
bcli company alias <name> <company-id> [--name <display-name>]
```

### company aliases

Show all configured aliases.

```bash
bcli company aliases
```

---

## endpoint

### endpoint list

List all known endpoints.

```bash
bcli endpoint list [--custom] [--standard] [--category <name>]
```

### endpoint search

Fuzzy search endpoints.

```bash
bcli endpoint search <query>
```

### endpoint info

Show detailed metadata for an endpoint.

```bash
bcli endpoint info <entity-set-name>
```

---

## registry

### registry import

Import custom API endpoints.

```bash
bcli registry import --from-postman <file.json> [--profile <name>]
bcli registry import --from-json <file.json> [--profile <name>]
bcli registry import --from-metadata [--profile <name>]
```

### registry list

Show imported registries.

```bash
bcli registry list
```

---

## get

Query records from an entity.

```bash
bcli get <endpoint> [<record-id>] [options]
```

| Option | Description |
|--------|-------------|
| `--filter <expr>` | OData $filter expression |
| `--select <fields>` | Comma-separated field names |
| `--expand <navs>` | Comma-separated navigation properties |
| `--orderby <expr>` | OData $orderby expression |
| `--top <n>` | Maximum records to return |
| `--skip <n>` | Records to skip |
| `--count` | Include total record count |
| `--all` | Follow pagination for all records |
| `--out <path>` | Write the record's media stream (raw bytes) to this path instead of printing records. Requires a record id |
| `--media <field>` | Media property to download (default: auto-discovered from the record's `@odata.mediaReadLink` annotations). Requires `--out` |
| `--overwrite` | Replace an existing `--out` file (refused by default) |
| `--publisher <name>` | Custom API publisher override |
| `--group <name>` | Custom API group override |
| `--version <name>` | Custom API version override |

Downloading a record's attachment:

```bash
# Find the record, then stream its media property to a file.
bcli get incomingDocuments --filter "description eq 'March invoice'" --top 1 -f json
bcli get incomingDocuments <systemId> --out invoice.pdf
```

`--out` is single-record mode and cannot be combined with the query-shaping
flags (`--filter`, `--select`, `--expand`, `--orderby`, `--top`, `--skip`,
`--count`, `--all`) or an explicit `--format`. The record is fetched through
normal endpoint resolution, so `disable_standard_api` and the endpoint registry
apply to a media download exactly as they do to a read. See
[querying.md](querying.md#downloading-media-streams-pdfs).

---

## action

Invoke an OData v4 bound action on a record: `POST <entitySet>(<key>)/<Namespace>.<action>`.

```bash
bcli action <endpoint> <record-key> <action-name> [options]
```

| Option | Description |
|--------|-------------|
| `--data <json>` | JSON body for the action (literal or `@file`). Defaults to an empty body |
| `--no-data` | Explicitly send an empty body — same as omitting `--data` |
| `--namespace <ns>` | Action namespace (default: `Microsoft.NAV`) |
| `--out <path>` | Decode the action's base64 return value and write the raw bytes here |
| `--overwrite` | Replace an existing `--out` file (refused by default) |
| `--result-out <path>` | Write the JSON result envelope to this path (atomic) |
| `--result-fd <n>` | Write the JSON result envelope to this file descriptor |
| `--idempotency-key <k>` | Opaque token forwarded as the `Idempotency-Key` header |
| `--yes` | Skip the read-only-profile warning prompt |

```bash
bcli action examples 42 archive
bcli action documents 42 renderPdf --out document.pdf
```

**`--out` and `--result-out` write different things.** `--out` writes the
action's *decoded payload bytes* — the PDF or export the action returned as
base64 in its `value` property. `--result-out` writes the *JSON result
envelope* describing the invocation (status, exit code, correlation id). They
compose; neither implies the other.

The destination is checked before the POST is sent, so an unwritable path fails
without invoking the action. An action that returns 204 No Content has no
payload to write, and `--out` reports that rather than creating an empty file.

---

## post

Create a new record.

```bash
bcli post <endpoint> --data <json-or-@file> [--publisher ...] [--group ...] [--version ...]
```

---

## patch

Update an existing record.

```bash
bcli patch <endpoint> <record-id> --data <json-or-@file> [--etag <tag>] [--publisher ...] [--group ...] [--version ...]
```

---

## delete

Delete a record.

```bash
bcli delete <endpoint> <record-id> [--etag <tag>] [--publisher ...] [--group ...] [--version ...]
```

---

## test

### test connection

Test auth and API reachability.

```bash
bcli test connection
```

### test auth

Test authentication only.

```bash
bcli test auth
```

### test endpoint

Test a specific endpoint (GET $top=1).

```bash
bcli test endpoint <entity-set-name>
```

---

## batch

### batch run

Execute a YAML batch file. Supports workflow-style step chaining, parameters, and result capture.

```bash
bcli batch run <file.yaml> [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--dry-run` | | Print resolved requests without executing |
| `--output <path>` | `-o` | Save full results (all steps + records + metadata) to a JSON file |
| `--format <fmt>` | `-f` | Print each step's returned data inline (`table`, `json`, `csv`, `ndjson`) |
| `--set key=value` | | Set a workflow parameter (repeatable). Values auto-typed (YAML scalar rules). |
| `--params <file>` | | Load workflow parameters from a YAML mapping file |

Examples:
```bash
# Preview only
bcli batch run workflow.yaml --dry-run

# Run with parameters and save JSON
bcli batch run workflow.yaml --set vendor=V00011 --set month=2026-03 -o results.json

# Run with a params file and print each step's rows as a table
bcli batch run workflow.yaml --params month-end.yaml -f table
```

See [Batch Operations](batch-operations.md) for step chaining, parameter syntax, and `${{ steps.<id>.data }}` references.

---

## ai-context

Dump LLM-ready usage instructions for the CLI. Useful for priming Claude / agents.

```bash
bcli ai-context [--format json|text]
```

Emits a compact reference covering command syntax, OData filter quirks, output formats, and common workflows. Pipe the output into a system prompt or save it as context for LLM agents.

---

## etl (optional — requires `bc-cli[etl]`)

Extract Business Central data via `dlt` pipelines. Available only when the `etl` extra is installed (`pip install 'bc-cli[etl]'`).

### etl entities

List entities available for ETL extraction.

```bash
bcli etl entities [--include-standard]
```

By default shows only custom API endpoints for the active profile. Pass `--include-standard` to include the 79 built-in v2.0 entities.

### etl sync

Extract data and load to a `dlt` destination.

```bash
bcli etl sync [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--entities <list>` | | Comma-separated entity names (default: all custom endpoints) |
| `--destination <dest>` | `-d` | dlt destination: `filesystem`, `duckdb`, `iceberg` (default: `filesystem`) |
| `--dataset <name>` | | Dataset name in destination (default: `bc_raw`) |
| `--pipeline <name>` | | Pipeline name for state tracking (default: `bcli_etl`) |
| `--full-refresh` | | Ignore cursor; reload everything |
| `--include-standard` | | Also sync standard v2.0 entities (skipped by default — usually handled by Fivetran) |

Examples:
```bash
bcli etl sync --destination filesystem
bcli etl sync --entities customers,vendors --destination duckdb
bcli etl sync --full-refresh --destination iceberg
```
