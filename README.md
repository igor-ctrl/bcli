# bcli

[![Tests](https://github.com/igor-ctrl/bcli/actions/workflows/tests.yml/badge.svg)](https://github.com/igor-ctrl/bcli/actions/workflows/tests.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)

A Python SDK and CLI for Microsoft Dynamics 365 Business Central APIs, with a built-in [dlt](https://dlthub.com) source for ETL backup pipelines.

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
# Install (PyPI distribution name is "bc-cli"; the binary is "bcli")
pip install bc-cli
# or
uv tool install bc-cli

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
- **ETL pipeline** — Built-in [dlt](https://dlthub.com) source for incremental backup to Parquet / DuckDB / Iceberg / Postgres
- **Structured logging** — JSON request logs with correlation IDs for observability

## ETL Pipeline (dlt source)

Sync BC data incrementally to any [dlt-supported destination](https://dlthub.com/docs/dlt-ecosystem/destinations/) — useful as a Fivetran backup, a warehouse backfill tool, or a standalone ETL runner.

Standalone (any BC tenant, no bcli config required):

```python
import dlt
from bcli.etl import business_central, EntityDef, fivetran_stamper

source = business_central(
    tenant_id="...", client_id="...", client_secret="...",
    environment="Production",
    entities=[
        EntityDef(name="customers"),
        EntityDef(name="vendors"),
    ],
    multi_company=True,             # iterate all BC companies
    stampers=[fivetran_stamper()],  # add _fivetran_synced / _fivetran_deleted
)

pipeline = dlt.pipeline(destination="duckdb", dataset_name="bc_raw")
pipeline.run(source)
```

Or use the bcli bridge (reuses your profile and custom-API registry):

```bash
# List entities the pipeline will sync
bcli --profile prod etl entities

# Incremental sync (uses systemModifiedAt cursor)
bcli --profile prod etl sync --destination filesystem

# Full refresh
bcli --profile prod etl sync --destination filesystem --full-refresh

# Schedule via cron (every 10 min incremental, nightly full refresh)
*/10 * * * * bcli --profile prod etl sync --destination filesystem
0 0 * * *    bcli --profile prod etl sync --destination filesystem --full-refresh
```

## Installation

Requires Python 3.11+.

> **Heads-up on the package name.** The PyPI name is **`bc-cli`**, not
> `bcli`. An unrelated 2018-era package squats on the `bcli` name (an "EC2
> Cluster Creator" with no relation to this project) — `pip install bcli`
> will install that, not this. Always `pip install bc-cli`. Once installed,
> the import name (`import bcli`) and the CLI binary (`bcli`) work as
> documented.

```bash
# SDK only (for libraries, MCP servers, Airflow DAGs)
pip install bc-cli

# SDK + CLI
pip install "bc-cli[cli]"

# SDK + ETL (dlt source for backup pipelines)
pip install "bc-cli[etl]"

# Everything
pip install "bc-cli[cli,etl]"

# Via uv (recommended)
uv tool install bc-cli

# From source
git clone https://github.com/igor-ctrl/bcli.git
cd bcli
pip install -e ".[dev,etl]"
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
