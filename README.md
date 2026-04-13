# bcli

A Python SDK and CLI for Microsoft Dynamics 365 Business Central APIs.

## SDK Quick Start

```python
from bcli import AsyncBCClient

# Programmatic auth — no config files needed
async with AsyncBCClient(
    tenant_id="your-tenant-id",
    client_id="your-app-id",
    client_secret="your-secret",
    environment="Sandbox",
    company_id="your-company-id",
) as client:
    # Query with fluent OData builder
    customers = await client.query("customers").filter("city eq 'Chicago'").top(10).get()

    # Write with safety gate
    async with client.safe_write("Sandbox", "your-company-id") as sw:
        await sw.post("salesInvoices", body={"customerNumber": "10000"}, domain="finance")
```

Or use TOML profiles for the CLI and repeated SDK use:

```python
from bcli import AsyncBCClient

async with AsyncBCClient(profile="production") as client:
    vendors = await client.query("vendors").select("displayName", "balance").get()
```

## CLI Quick Start

```bash
# Install
pip install bcli
# or
uv tool install bcli

# Configure (interactive — discovers companies automatically)
bcli config init

# Query standard APIs immediately
bcli get customers --top 5
bcli get vendors --filter "displayName eq 'Fabrikam'" --format json
bcli get salesInvoices --select number,totalAmountIncludingTax --top 10

# Import custom APIs from a Postman collection
bcli registry import --from-postman ./my_collection.json

# Query custom endpoints (route auto-resolved)
bcli get myCustomEntities --top 5
```

## Features

- **Works out of the box** — 79 standard BC v2.0 entities (customers, vendors, items, GL entries, ...) with zero configuration beyond auth
- **Custom API support** — Import your custom API pages from Postman collections, JSON, or live `$metadata`
- **Three-tier endpoint resolution** — Custom registry -> standard v2.0 -> fuzzy suggestions
- **Multi-company** — Assign aliases to companies and query across all entities
- **OData query builder** — `--filter`, `--select`, `--expand`, `--orderby`, `--top`, `--skip` on every query
- **Multiple output formats** — table, JSON, CSV, NDJSON for pipeline use
- **Secure auth** — OS keychain integration (macOS Keychain, Windows Credential Manager), token caching, client credentials + device code flows
- **Write safety** — SafeContext gate prevents wrong-environment writes, enforces draft status on financial documents
- **Programmatic auth** — Pass credentials directly for MCP servers, Airflow DAGs, and containers (no config files required)
- **Batch operations** — Execute sequences of API calls from YAML files
- **Structured logging** — JSON request logs with correlation IDs for observability

## Installation

Requires Python 3.11+.

```bash
# SDK only (for libraries, MCP servers, Airflow DAGs)
pip install bcli

# SDK + CLI
pip install "bcli[cli]"

# Via uv (recommended)
uv tool install bcli

# From source
git clone https://github.com/igor-ctrl/bc-cli.git
cd bc-cli
pip install -e ".[dev]"
```

## Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | First-time setup, authentication, your first query |
| [Configuration](docs/configuration.md) | Profiles, environments, config file format |
| [Authentication](docs/authentication.md) | Client credentials, device code, OS keychain |
| [Querying Data](docs/querying.md) | GET, OData filters, pagination, output formats |
| [Write Operations](docs/write-operations.md) | POST, PATCH, DELETE |
| [Custom APIs](docs/custom-apis.md) | Importing from Postman, JSON, or $metadata |
| [Multi-Company](docs/multi-company.md) | Company aliases, cross-entity queries |
| [Batch Operations](docs/batch-operations.md) | YAML batch files |
| [SDK Usage](docs/sdk-usage.md) | Python SDK for developers and MCP servers |
| [Command Reference](docs/command-reference.md) | Complete CLI command reference |
| [Contributing](docs/contributing.md) | Development setup, architecture, testing |

## License

MIT
