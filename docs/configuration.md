# Configuration

## Config File

bcli stores configuration at `~/.config/bcapi/config.toml`.

```toml
[defaults]
profile = "production"
format = "table"
page_size = 100
timeout = 60

[profiles.production]
tenant_id = "c6aabf12-1e7a-410a-bd33-c09d6cb294d7"
environment = "Production"
company_id = "f99bd320-b400-4189-b3c1-c62c05d4e7a5"
company_name = "CRONUS USA, Inc."
auth_method = "client_credentials"
client_id = "48074c7f-5706-40d8-aa7d-7be7b33e2df7"
client_secret_env = "BCAPI_SECRET"

[profiles.sandbox]
tenant_id = "c6aabf12-1e7a-410a-bd33-c09d6cb294d7"
environment = "Sandbox"
company_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
auth_method = "client_credentials"
client_id = "48074c7f-5706-40d8-aa7d-7be7b33e2df7"
client_secret_env = "BCAPI_SANDBOX_SECRET"
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
bcli config set profiles.sandbox.client_secret_env "BCAPI_SANDBOX_SECRET"
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

1. **Global config** — `~/.config/bcapi/config.toml`
2. **Project config** — `.bcapi.toml` in the current directory or any parent (useful for per-project defaults)
3. **Environment variables** — `BCAPI_PROFILE`, `BCAPI_FORMAT`, `BCAPI_TIMEOUT`
4. **CLI flags** — `--profile`, `--env`, `--company`, `--format`

## Project-Level Config

Create a `.bcapi.toml` in your project directory to override defaults for that project:

```toml
[defaults]
profile = "sandbox"
format = "json"
```

Anyone working in that directory will automatically use those settings.

## Environment Variables

| Variable | Overrides |
|----------|-----------|
| `BCAPI_PROFILE` | `defaults.profile` |
| `BCAPI_FORMAT` | `defaults.format` |
| `BCAPI_TIMEOUT` | `defaults.timeout` |
| `BCAPI_SECRET` | Generic fallback client secret |
| `BCAPI_CLIENT_SECRET` | Generic fallback client secret |

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
| `~/.config/bcapi/config.toml` | Main configuration |
| `~/.config/bcapi/tokens.json` | Cached auth tokens |
| `~/.config/bcapi/registries/*.json` | Imported custom API registries |
| `.bcapi.toml` | Project-level config override |
