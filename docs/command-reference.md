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

Interactive setup wizard. Discovers companies automatically.

```bash
bcli config init
```

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
bcli auth login
```

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
| `--publisher <name>` | Custom API publisher override |
| `--group <name>` | Custom API group override |
| `--version <name>` | Custom API version override |

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

Execute a YAML batch file.

```bash
bcli batch run <file.yaml> [--dry-run]
```
