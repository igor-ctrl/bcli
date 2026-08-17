# Authentication

bcli supports three Business Central online authentication methods:

| Method | Flow | Use case |
|--------|------|----------|
| `browser` | Authorization code with PKCE | Default for local humans and AI agents |
| `client_credentials` | App permissions | CI/CD, servers, scheduled jobs |
| `device_code` | Delegated device code | SSH/headless fallback |

For a full zero-knowledge Entra ID and Business Central setup, start with
[Business Central Admin Setup](business-central-admin-setup.md).

## Browser Auth (Recommended)

Browser auth is the default for `bcli config init`. It opens the user's browser,
uses PKCE, needs no client secret, and Business Central enforces the signed-in
user's permission sets.

```bash
bcli config init
bcli auth login
bcli get customers --top 5
```

The Entra app registration must be a public/native client with delegated
Business Central permissions and a localhost redirect URI. See
[Business Central Admin Setup](business-central-admin-setup.md) for the portal
steps.

Useful login options:

```bash
# Fresh browser session, useful when switching accounts
bcli auth login --method browser --incognito

# Explicit browser profile setup
bcli config init --auth browser
```

## Client Credentials

Use client credentials only for automation: CI/CD, background jobs, servers, and
scheduled exports. This path uses application permissions and a client secret,
so it should be set up deliberately.

```bash
bcli config init --automation
bcli auth store-secret
bcli get customers --top 5
```

bcli never stores client secrets in config files. It resolves secrets in this
order:

1. OS keychain: macOS Keychain, Windows Credential Manager, or equivalent.
2. The configured `client_secret_env` environment variable.
3. Generic fallback env vars: `BCLI_SECRET` or `BCLI_CLIENT_SECRET`.

In CI, use environment variables:

```yaml
env:
  BCLI_SECRET: ${{ secrets.BC_CLIENT_SECRET }}

steps:
  - run: bcli get customers --top 1 -f json -q
```

No `bcli auth login` is required when a valid secret is available; bcli acquires
tokens automatically.

## Device Code

Device code is a fallback for hosts where a localhost browser callback is not
practical, such as SSH sessions or locked-down remote machines.

```bash
bcli config init --headless
bcli auth login --method device
```

The terminal prints a URL and code. Open the URL in any browser, enter the code,
and bcli caches the resulting delegated token.

## Token Cache

bcli keeps two caches, both `0600` in a `0700` directory:

| File | Holds | Lifetime |
|------|-------|----------|
| `~/.config/bcli/tokens.json` | access tokens | ~1 hour (5-minute expiry buffer) |
| `~/.config/bcli/msal_cache.json` | MSAL's cache, incl. **refresh tokens** | until revoked or expired |

The second one is what keeps interactive auth rare. When an access token
expires, the `browser` and `device_code` flows renew silently from the cached
refresh token instead of prompting — so you sign in about once per
refresh-token lifetime, not once an hour.

```bash
bcli auth status   # reports whether silent renewal is available
bcli auth logout   # clears both caches — after this, the next command prompts
```

`bcli auth logout` deliberately removes the MSAL cache too. Dropping only the
access token would leave the refresh token in place, so the next command would
renew silently and the "logout" would not have logged you out.

Client-credentials profiles are unaffected: a service principal mints a fresh
token from its secret on demand and has no refresh token to persist.

## Common Failures

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Redirect URI mismatch | Entra app lacks localhost redirect URI | Add `http://localhost` as a mobile/desktop redirect URI |
| Consent required | Tenant admin has not granted API consent | Grant admin consent for Business Central delegated permissions |
| 403 Forbidden | User lacks BC permission set for that page/company | Assign the user the required Business Central permissions |
| Wrong tenant/account | Browser reused an existing Microsoft session | Re-run with `--incognito` |
| Secret missing | Automation profile cannot find `BCLI_SECRET` or keychain secret | Run `bcli auth store-secret` or set the env var |
