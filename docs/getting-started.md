# Getting Started

## Prerequisites

- Python 3.11 or later
- A Business Central online environment
- An Entra app registration configured for bcli
- Business Central permissions for the signed-in user

If you do not already know how to create the Entra app or assign Business
Central permissions, use [Business Central Admin Setup](business-central-admin-setup.md)
first.

## Install

The PyPI distribution name is **`bc-cli`**. Once installed, the binary is
`bcli`.

```bash
uv tool install bc-cli
# or
pip install bc-cli
```

Verify the installation:

```bash
bcli --version
```

## Local Human Or Agent Setup

Browser auth is the default. It uses your Microsoft sign-in, needs no client
secret, and Business Central enforces your normal permission sets.

```bash
bcli config init
```

You'll be prompted for:

| Prompt | What to enter |
|--------|--------------|
| Profile name | A name for this connection, such as `production` or `sandbox` |
| Tenant ID | Your Entra tenant ID |
| Environment name | BC environment name, such as `Production` or `Sandbox` |
| Client ID | The Entra app registration's Application (client) ID |

When prompted, authenticate in the browser so bcli can discover companies and
set a default company.

## Automation Setup

For CI/CD, servers, and scheduled jobs, use client credentials:

```bash
bcli config init --automation
bcli auth store-secret
```

This path requires an Entra app with application permissions and either an OS
keychain secret or an environment variable such as `BCLI_SECRET`.

## Headless Setup

For SSH sessions where browser callback auth cannot work:

```bash
bcli config init --headless
bcli auth login --method device
```

## Your First Query

```bash
bcli get customers --top 5
bcli get vendors --filter "displayName eq 'Fabrikam'"
bcli get items --select number,displayName,unitPrice --top 10
bcli -f json get salesInvoices --top 3
```

## Explore Available Endpoints

```bash
bcli endpoint search vendor
bcli endpoint info customers
bcli endpoint fields customers
```

For custom APIs, import the registry first:

```bash
bcli registry import --from-postman ./my_collection.json
bcli get myCustomEntities --top 5
```

## Test Your Connection

```bash
bcli test connection
bcli test auth
bcli test endpoint customers
```

## Next Steps

- [Business Central Admin Setup](business-central-admin-setup.md) — Entra and BC setup from scratch
- [Authentication](authentication.md) — Browser, automation, and headless auth
- [Configuration](configuration.md) — Profiles, environments, and config files
- [Custom APIs](custom-apis.md) — Import custom API pages
- [Saved Queries](saved-queries.md) — Named business questions with no OData
- [MCP Server](mcp-server.md) — Use bcli from MCP-aware agents
