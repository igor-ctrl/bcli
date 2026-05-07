# Configuration

## Config File

bcli stores configuration at `~/.config/bcli/config.toml`.

```toml
[defaults]
profile = "production"
format = "table"
page_size = 100
timeout = 60

[profiles.production]
tenant_id = "REDACTED-TENANT-ID"
environment = "Production"
company_id = "REDACTED-COMPANY-ID"
company_name = "CRONUS USA, Inc."
auth_method = "browser"
client_id = "48074c7f-5706-40d8-aa7d-7be7b33e2df7"

[profiles.automation]
tenant_id = "REDACTED-TENANT-ID"
environment = "Production"
company_id = "REDACTED-COMPANY-ID"
auth_method = "client_credentials"
client_id = "9a12d8e3-1111-2222-3333-7be7b33e2df7"
client_secret_env = "BCLI_SECRET"
```

## Profiles

Profiles let you manage multiple BC connections: different tenants,
environments, companies, or auth modes.

### Create A Profile

```bash
# Local human/agent use: browser auth
bcli config init

# Automation/CI/server use: client credentials
bcli config init --automation

# SSH/headless fallback: device code
bcli config init --headless
```

Manual setup also works:

```bash
bcli config set profiles.sandbox.tenant_id "your-tenant-id"
bcli config set profiles.sandbox.environment "Sandbox"
bcli config set profiles.sandbox.auth_method "browser"
bcli config set profiles.sandbox.client_id "your-client-id"
```

### Switch Profiles

```bash
bcli config use production
bcli config use sandbox
bcli -p sandbox get customers --top 5
```

### View Config

```bash
bcli config show
```

## Scoped Profiles

Scoped profiles are useful for domain teams. They hide the standard v2.0
catalog and show only imported custom endpoints.

```bash
bcli config init --profile ops --scoped --import warehouse.postman_collection.json
```

Scoped profiles still use browser auth by default. Use `--headless` only when a
localhost browser callback is not possible.

## Config Resolution Order

bcli merges configuration from multiple sources. Later sources override earlier
ones:

1. Global config: `~/.config/bcli/config.toml`
2. Project config: `.bcli.toml` in the current directory or a parent
3. Environment variables: `BCLI_PROFILE`, `BCLI_FORMAT`, `BCLI_TIMEOUT`
4. CLI flags: `--profile`, `--env`, `--company`, `--format`

## Project-Level Config

Create a `.bcli.toml` in your project directory to override defaults for that
project:

```toml
[defaults]
profile = "sandbox"
format = "json"
```

## Environment Variables

| Variable | Overrides |
|----------|-----------|
| `BCLI_PROFILE` | `defaults.profile` |
| `BCLI_FORMAT` | `defaults.format` |
| `BCLI_TIMEOUT` | `defaults.timeout` |
| `BCLI_SECRET` | Generic fallback client secret |
| `BCLI_CLIENT_SECRET` | Generic fallback client secret |

## Custom API Defaults

If you frequently query custom APIs without importing a registry, you can set
route defaults:

```toml
[profiles.production]
api_publisher = "mycompany"
api_group = "integration"
api_version = "v1.0"
```

Imported endpoint registries are preferred; route defaults are only an escape
hatch for ad-hoc access.

## Audit Log

Optional, opt-in audit trail for write operations. When enabled, every CLI write
(POST / PATCH / DELETE / attach upload) appends one JSONL line to a per-profile
file. Captures the resolved URL, response status, BC correlation ID, latency,
and outcome — enough to reconstruct what happened (and what was attempted).

Add an `[audit]` block to `~/.config/bcli/config.toml`:

```toml
[audit]
enabled = true
backend = "jsonl"
path = "~/.config/bcli/audit/{profile}.jsonl"   # default; {profile} interpolated
max_size_mb = 50
include_reads = false
redact_keys = ["password", "secret", "token", "key", "apiKey", "authorization"]
```

| Setting | Default | What it does |
|---------|---------|--------------|
| `enabled` | `false` | Master switch. When false, the audit code path is a no-op. |
| `backend` | `"jsonl"` | `"jsonl"` (file) or `"null"` (drop). |
| `path` | `~/.config/bcli/audit/{profile}.jsonl` | File location. `{profile}` is interpolated to the active profile name. |
| `max_size_mb` | `50` | Rotation threshold. When the file exceeds this, the existing content moves to `<path>.1` and a fresh file is started. Only one backup is kept. |
| `include_reads` | `false` | Reserved for a future release; currently writes only. |
| `redact_keys` | `["password", "secret", ...]` | Substring-matched (case-insensitive) on request-body field names. Matched values are replaced with `***REDACTED***` before write. |

Each entry is one JSON object with the following keys:

```json
{
  "ts": "2026-05-06T10:00:00Z",
  "profile": "production",
  "environment": "Production",
  "company_id": "<company-id>",
  "method": "POST",
  "endpoint": "customers",
  "resolved_url": "https://api.businesscentral.dynamics.com/.../customers",
  "record_id": null,
  "request_body": {"displayName": "Test"},
  "status": 201,
  "correlation_id": "abc-...",
  "latency_ms": 312,
  "cli_version": "0.2.0",
  "caller": "cli",
  "outcome": "completed",
  "error": null
}
```

`outcome` is one of:

- `completed` — the write succeeded.
- `failed` — the write raised; `status`, `correlation_id`, and `error` capture
  what BC said.
- `dry_run` — the user passed `--dry-run`; no HTTP call fired but the intent
  is recorded.

The SDK (`AsyncBCClient`) does NOT auto-emit. Audit is a CLI-layer ergonomic;
programmatic SDK users get unfiltered access by design and can wire their own
logging.

## File Locations

| File | Purpose |
|------|---------|
| `~/.config/bcli/config.toml` | Main configuration |
| `~/.config/bcli/tokens.json` | Cached auth tokens |
| `~/.config/bcli/registries/*.json` | Imported custom API registries |
| `~/.config/bcli/queries/*.yaml` | Saved queries |
| `~/.config/bcli/audit/*.jsonl` | Per-profile audit log (when `[audit] enabled = true`) |
| `.bcli.toml` | Project-level config override |
