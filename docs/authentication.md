# Authentication

bcli supports four authentication methods for Business Central online:

| Method | Flow | Use case |
|--------|------|----------|
| `client_credentials` | App permissions (service-to-service) | CI/CD, MCP servers, background jobs |
| `browser` | Authorization code with PKCE (delegated) | Interactive dev, individual user permissions |
| `device_code` | Device code (delegated) | Headless hosts, SSH sessions, non-browser envs |
| `workos` | WorkOS AuthKit SSO → role-based BC client | Teams with role-based access control |

Select a method per profile via `auth_method` or override per call with `bcli auth login --method <method>`.

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
3. **Generic fallback** — `BCLI_SECRET` or `BCLI_CLIENT_SECRET` env vars

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
export BCLI_SECRET="your-secret-here"
```

### Token Caching

After authentication, bcli caches the access token at `~/.config/bcli/tokens.json`. Tokens are reused until 5 minutes before expiry (~55 minutes for BC tokens). While a cached token is valid, no secret is needed.

```bash
# Check token status
bcli auth status

# Force re-authentication
bcli auth logout
bcli auth login
```

## Browser (Authorization Code with PKCE)

Interactive browser-based OAuth. The user authenticates in their default browser; bcli captures the redirect via a local loopback on port 8400. Uses PKCE — no client secret needed, and delegated permissions mean the token carries the *user's* BC permissions, not app-wide ones.

### Setup

```bash
bcli config set profiles.interactive.auth_method browser
bcli config set profiles.interactive.tenant_id "your-tenant-id"
bcli config set profiles.interactive.client_id "your-client-id"
bcli config set profiles.interactive.environment "Production"
```

The app registration must:
- Have delegated permissions for Business Central (`user_impersonation` / `Financials.ReadWrite.All`)
- Include `http://localhost:8400/callback` as a redirect URI
- Be configured as a public client (no client secret required)

### Usage

```bash
bcli -p interactive auth login --method browser

# Fresh session (no cached browser login) — useful for switching accounts:
bcli -p interactive auth login --method browser --incognito
```

If a `login_hint` is set in your profile (e.g. via WorkOS), the BC account picker is skipped automatically.

## Device Code (Interactive)

For interactive use where a user is present but a browser redirect is not practical (SSH, headless hosts). The user authenticates via a browser on any device — no client secret needed.

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
bcli -p interactive auth login --method device
# Prints a URL and code — open the URL in your browser and enter the code
```

The app registration must have delegated permissions (not application permissions) for device code flow to work.

## WorkOS AuthKit (Role-Based BC Access)

Two-step auth: users authenticate via WorkOS SSO, then their WorkOS group determines which BC client_id they use for the BC browser flow. Useful for teams where different roles need different BC app registrations (e.g. `finance-read-only` vs `finance-write`).

### Flow

1. User authenticates via WorkOS AuthKit (browser redirect)
2. bcli looks up the user's WorkOS group membership
3. bcli maps the group to a BC `client_id` via the profile's `workos.groups` table
4. BC browser OAuth runs with the resolved `client_id` (PKCE, delegated)

The WorkOS identity is cached at `~/.config/bcli/workos_identity.json` until it expires.

### Setup

```toml
[profiles.acme]
tenant_id = "REDACTED-..."
environment = "Production"
auth_method = "workos"

[profiles.acme.workos]
api_key_env = "WORKOS_API_KEY"
client_id = "client_01ABC..."

[profiles.acme.workos.groups]
"finance-read"  = "48074c7f-..."   # BC app registration for read-only finance role
"finance-write" = "9a12d8e3-..."   # BC app registration for write finance role
"ops-full"      = "bf441e7a-..."
```

Install the `cli` extra (WorkOS SDK is included):

```bash
pip install "bcli[cli]"
```

### Usage

```bash
bcli -p acme auth login --method workos

# Switch to a different user without touching OS browser sessions:
bcli -p acme auth login --method workos --incognito
```

## Auth Commands

```bash
bcli auth login [--method ...] [-i]    # Authenticate and cache token (see command-reference.md)
bcli auth status                        # Show token and keychain status
bcli auth logout                        # Clear cached tokens
bcli auth store-secret                  # Store secret in OS keychain
bcli auth delete-secret                 # Remove secret from OS keychain
```

## CI/CD Usage

For CI/CD pipelines, use environment variables:

```yaml
# GitHub Actions example
env:
  BCLI_SECRET: ${{ secrets.BC_CLIENT_SECRET }}

steps:
  - run: bcli get customers --top 1 -f json -q
```

No `bcli auth login` is needed — bcli acquires tokens automatically when a secret is available.
