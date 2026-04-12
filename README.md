# bcli

A Python SDK and CLI for Microsoft Dynamics 365 Business Central APIs.

**bcli** gives you a single command to query any Business Central API — standard or custom — without constructing URLs, managing tokens, or writing scripts. It ships with 79 standard v2.0 entities ready to go and supports importing custom APIs from Postman collections.

## Features

- **Works out of the box** — 79 standard BC v2.0 entities (customers, vendors, items, GL entries, ...) with zero configuration beyond auth
- **Custom API support** — Import your custom API pages from Postman collections, JSON, or live `$metadata`
- **Multi-company** — Assign aliases to companies ("LLC", "Corp") and query across all entities with `--company all`
- **OData query builder** — `--filter`, `--select`, `--expand`, `--orderby`, `--top`, `--skip` on every query
- **Multiple output formats** — table, JSON, CSV, NDJSON for pipeline use
- **Secure auth** — OS keychain integration (macOS Keychain, Windows Credential Manager), token caching, client credentials + device code flows
- **Batch operations** — Execute sequences of API calls from YAML files
- **Python SDK** — Use `from bcapi import BCClient` in your own code, MCP servers, or Airflow DAGs

## Quick Start

```bash
# Install
pip install bcapi
# or
uv tool install bcapi

# Configure (interactive — discovers companies automatically)
bcli config init

# Query standard APIs immediately
bcli get customers --top 5
bcli get vendors --filter "displayName eq 'Fabrikam'" --format json
bcli get salesInvoices --select number,totalAmountIncludingTax --top 10

# Import custom APIs from a Postman collection
bcli registry import --from-postman ./my_collection.json

# Query custom endpoints (route auto-resolved)
bcli get engineOverviews --top 5
```

## Installation

Requires Python 3.11+.

```bash
# Via pip
pip install bcapi

# Via uv (recommended)
uv tool install bcapi

# From source
git clone https://github.com/igor-ctrl/bc-cli.git
cd bc-cli
uv tool install -e .
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

## SDK Usage (Quick)

```python
from bcapi import BCClient

client = BCClient(profile="production")

# Query with fluent OData builder
records = client.query("customers").filter("city eq 'Chicago'").top(10).get()

# Async for MCP servers
from bcapi import AsyncBCClient

async with AsyncBCClient(profile="production") as client:
    vendors = await client.query("vendors").select("displayName", "balance").get()
```

## License

MIT
