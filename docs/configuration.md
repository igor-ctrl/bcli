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

## File Locations

| File | Purpose |
|------|---------|
| `~/.config/bcli/config.toml` | Main configuration |
| `~/.config/bcli/tokens.json` | Cached auth tokens |
| `~/.config/bcli/registries/*.json` | Imported custom API registries |
| `~/.config/bcli/queries/*.yaml` | Saved queries |
| `.bcli.toml` | Project-level config override |
