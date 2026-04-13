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
auth_method = "client_credentials"
client_id = "48074c7f-5706-40d8-aa7d-7be7b33e2df7"
client_secret_env = "BCLI_SECRET"

[profiles.sandbox]
tenant_id = "REDACTED-TENANT-ID"
environment = "Sandbox"
company_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
auth_method = "client_credentials"
client_id = "48074c7f-5706-40d8-aa7d-7be7b33e2df7"
client_secret_env = "BCLI_SANDBOX_SECRET"
```

## Profiles

Profiles let you manage multiple BC connections — different tenants, environments, or app registrations.

### Create a Profile

```bash
# Interactive
bcli config init
# Type a new profile name when prompted

# Manual
bcli config set profiles.sandbox.tenant_id "your-tenant-id"
bcli config set profiles.sandbox.environment "Sandbox"
bcli config set profiles.sandbox.client_id "your-client-id"
bcli config set profiles.sandbox.client_secret_env "BCLI_SANDBOX_SECRET"
```

### Switch Profiles

```bash
# Set the default profile
bcli config use production
bcli config use sandbox

# Use a profile for a single command
bcli -p sandbox get customers --top 5
```

### View Config

```bash
bcli config show
```

## Config Resolution Order

bcli merges configuration from multiple sources. Later sources override earlier ones:

1. **Global config** — `~/.config/bcli/config.toml`
2. **Project config** — `.bcli.toml` in the current directory or any parent (useful for per-project defaults)
3. **Environment variables** — `BCLI_PROFILE`, `BCLI_FORMAT`, `BCLI_TIMEOUT`
4. **CLI flags** — `--profile`, `--env`, `--company`, `--format`

## Project-Level Config

Create a `.bcli.toml` in your project directory to override defaults for that project:

```toml
[defaults]
profile = "sandbox"
format = "json"
```

Anyone working in that directory will automatically use those settings.

## Environment Variables

| Variable | Overrides |
|----------|-----------|
| `BCLI_PROFILE` | `defaults.profile` |
| `BCLI_FORMAT` | `defaults.format` |
| `BCLI_TIMEOUT` | `defaults.timeout` |
| `BCLI_SECRET` | Generic fallback client secret |
| `BCLI_CLIENT_SECRET` | Generic fallback client secret |

## Custom API Defaults

If you frequently query custom APIs without importing a registry, you can set defaults:

```toml
[profiles.production]
api_publisher = "mycompany"
api_group = "integration"
api_version = "v1.0"
```

These are used when an endpoint isn't found in any registry and no `--publisher/--group/--version` flags are provided.

## File Locations

| File | Purpose |
|------|---------|
| `~/.config/bcli/config.toml` | Main configuration |
| `~/.config/bcli/tokens.json` | Cached auth tokens |
| `~/.config/bcli/registries/*.json` | Imported custom API registries |
| `.bcli.toml` | Project-level config override |
