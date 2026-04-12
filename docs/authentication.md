# Authentication

bcli supports two authentication methods for Business Central online.

## Client Credentials (Service-to-Service)

The default method. Uses an Azure Entra ID app registration with application permissions.

### Prerequisites

1. An app registration in Azure Entra ID
2. `API.ReadWrite.All` application permission granted for Dynamics 365 Business Central
3. A client secret generated for the app registration

### Setup

```bash
bcli config init
# Provide tenant ID, client ID, and the name of the env var holding your secret
```

### Secret Storage

bcli never stores secrets in config files. Instead, it resolves secrets at runtime in this order:

1. **OS Keychain** (recommended) — macOS Keychain, Windows Credential Manager
2. **Environment variable** — Referenced by name in `client_secret_env`
3. **Generic fallback** — `BCAPI_SECRET` or `BCAPI_CLIENT_SECRET` env vars

#### Store in Keychain (Recommended)

```bash
bcli auth store-secret
# Prompted for the secret (hidden input)
```

This is the best option because:
- The secret persists across shell sessions
- No env var to manage
- Works with any tool that runs bcli (Claude Code, scripts, cron)
- Secured by OS-level encryption

#### Store as Environment Variable

```bash
# In your shell profile (~/.zshrc or ~/.bashrc)
export BCAPI_SECRET="your-secret-here"
```

### Token Caching

After authentication, bcli caches the access token at `~/.config/bcapi/tokens.json`. Tokens are reused until 5 minutes before expiry (~55 minutes for BC tokens). While a cached token is valid, no secret is needed.

```bash
# Check token status
bcli auth status

# Force re-authentication
bcli auth logout
bcli auth login
```

## Device Code (Interactive)

For interactive use where a user is present. The user authenticates via a browser — no client secret needed.

### Setup

Set `auth_method = "device_code"` in your profile:

```bash
bcli config set profiles.interactive.auth_method device_code
bcli config set profiles.interactive.tenant_id "your-tenant-id"
bcli config set profiles.interactive.client_id "your-client-id"
bcli config set profiles.interactive.environment "Production"
```

### Usage

```bash
bcli -p interactive auth login
# Prints a URL and code — open the URL in your browser and enter the code
```

The app registration must have delegated permissions (not application permissions) for device code flow to work.

## Auth Commands

```bash
bcli auth login           # Authenticate and cache token
bcli auth status          # Show token and keychain status
bcli auth logout          # Clear cached tokens
bcli auth store-secret    # Store secret in OS keychain
bcli auth delete-secret   # Remove secret from OS keychain
```

## CI/CD Usage

For CI/CD pipelines, use environment variables:

```yaml
# GitHub Actions example
env:
  BCAPI_SECRET: ${{ secrets.BC_CLIENT_SECRET }}

steps:
  - run: bcli get customers --top 1 -f json -q
```

No `bcli auth login` is needed — bcli acquires tokens automatically when a secret is available.
