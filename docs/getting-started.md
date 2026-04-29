# Getting Started

## Prerequisites

- Python 3.11 or later
- A Business Central online environment
- An Azure Entra ID (Azure AD) app registration with `API.ReadWrite.All` application permission for BC

## Install

The PyPI distribution name is **`bc-cli`** (not `bcli` — that name is squatted
by an unrelated 2018 EC2-cluster package). Once installed, the CLI binary is
still `bcli`.

```bash
# Recommended
uv tool install bc-cli

# Or via pip
pip install bc-cli
```

Verify the installation:

```bash
bcli --version
```

## First-Time Setup

Run the interactive setup wizard:

```bash
bcli config init
```

You'll be prompted for:

| Prompt | What to enter |
|--------|--------------|
| Profile name | A name for this connection (e.g., `production`, `sandbox`) |
| Tenant ID | Your Azure AD tenant ID (GUID) |
| Environment name | BC environment name (e.g., `Production`, `Sandbox`) |
| Client ID | The app registration's Application (client) ID |
| Client secret env var name | Name of an env var holding the secret (e.g., `BCLI_SECRET`) |

After authenticating, bcli discovers all companies in your environment and lets you pick a default:

```
✓ Authenticating...
✓ Discovering companies...

  #  Company Name              Company ID
  1  CRONUS USA, Inc.          f99bd320-b400-...
  2  My Company                a1b2c3d4-e5f6-...

? Select default company [1]: 1

✓ Config saved to ~/.config/bcli/config.toml
✓ Standard v2.0 APIs ready (79 entities)
```

## Store Your Secret Securely

Instead of using environment variables, store the secret in your OS keychain:

```bash
bcli auth store-secret
# Enter your client secret (hidden input)
```

This stores the secret in macOS Keychain (or Windows Credential Manager). No env vars needed after this.

## Your First Query

```bash
# List customers
bcli get customers --top 5

# Filter with OData
bcli get vendors --filter "displayName eq 'Fabrikam'"

# Select specific fields
bcli get items --select number,displayName,unitPrice --top 10

# Output as JSON (for piping to jq)
bcli -f json get salesInvoices --top 3
```

## Explore Available Endpoints

```bash
# List all standard v2.0 endpoints
bcli endpoint list

# Search for an endpoint
bcli endpoint search vendor

# Get details about an endpoint
bcli endpoint info customers
```

## Test Your Connection

```bash
bcli test connection    # Test auth + API reachability
bcli test auth          # Test auth only
bcli test endpoint customers   # Test a specific endpoint
```

## Next Steps

- [Configuration](configuration.md) — Set up multiple profiles and environments
- [Authentication](authentication.md) — Device code flow, keychain details
- [Custom APIs](custom-apis.md) — Import your custom API pages
- [Multi-Company](multi-company.md) — Set up company aliases
- [Demo Setup (CRONUS)](demo-setup.md) — Stand up a free sandbox with Microsoft's demo company
