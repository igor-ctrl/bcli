# SDK Usage

The `bcli` Python SDK can be used directly in your code — MCP servers, Airflow DAGs, scripts, or any Python application.

## Install

The PyPI distribution name is `bc-cli`; the import name stays `bcli`.

```bash
pip install bc-cli
```

## Sync Client

```python
from bcli import BCClient

client = BCClient(profile="production")

# Query with fluent builder
records = client.query("customers").filter("city eq 'Chicago'").top(10).get()
for record in records:
    print(record["displayName"])

# Get a single record
response = client.get("customers", "a1b2c3d4-...")
print(response.raw)

# Create a record
result = client.post("customers", {
    "displayName": "New Customer",
    "email": "new@example.com",
})

# Update a record
client.patch("customers", "a1b2c3d4-...", {
    "email": "updated@example.com",
})

# Delete a record
client.delete("customers", "a1b2c3d4-...")
```

## Async Client

For MCP servers and async applications:

```python
from bcli import AsyncBCClient

async with AsyncBCClient(profile="production") as client:
    # Fluent query
    records = await client.query("customers").top(5).get()

    # Full response object
    response = await client.query("salesInvoices") \
        .filter("totalAmountIncludingTax gt 1000") \
        .select("number", "totalAmountIncludingTax") \
        .orderby("totalAmountIncludingTax desc") \
        .execute()

    print(f"Count: {response.count}")
    print(f"Next page: {response.next_link}")
    for record in response:
        print(record)
```

## Pagination

```python
# Automatic pagination (async)
async with AsyncBCClient(profile="production") as client:
    pages = await client.query("generalLedgerEntries") \
        .filter("postingDate ge 2024-01-01") \
        .pages()

    async for page in pages:
        for entry in page:
            process(entry)
```

## Company Discovery

```python
client = BCClient(profile="production")

# List all companies
companies = client.list_companies()
for company in companies:
    print(f"{company['name']}: {company['id']}")

# Test connection
if client.test_connection():
    print("Connected!")
```

## Endpoint Registry

```python
from bcli import EndpointRegistry

registry = EndpointRegistry(profile_name="production")

# Look up an endpoint
ep = registry.get("customers")
print(ep.route_display)  # "v2.0 (standard)"
print(ep.supports)       # ["GET", "POST", "PATCH", "DELETE"]

# Search
results = registry.search("vendor")
for ep in results:
    print(f"{ep.entity_set_name}: {ep.description}")

# List all
for ep in registry.list_all():
    print(ep.entity_set_name)
```

## Custom API Routes

```python
# Via registry (auto-resolved)
records = client.query("engineOverviews").top(5).get()

# Explicit route override
records = client.query("myEntity").route("mycompany", "api", "v1.0").top(5).get()

# Direct with route params
response = client.get(
    "myEntity",
    publisher="mycompany",
    group="api",
    version="v1.0",
)
```

## OData Query Builder

The `Query` class can be used standalone:

```python
from bcli.odata import Query

q = Query() \
    .filter("status eq 'Active'") \
    .filter("city eq 'Chicago'") \
    .select("id", "displayName", "status") \
    .orderby("displayName") \
    .top(50)

# Get OData params as dict
params = q.to_params()
# {'$filter': "(status eq 'Active') and (city eq 'Chicago')",
#  '$select': 'id,displayName,status',
#  '$orderby': 'displayName',
#  '$top': '50'}

# Get as query string
qs = q.to_query_string()
# "?$filter=...&$select=...&$orderby=...&$top=50"
```

## Error Handling

```python
from bcli import BCClient
from bcli.errors import (
    AuthError,
    NotFoundError,
    ThrottledError,
    ValidationError,
)

client = BCClient(profile="production")

try:
    records = client.query("customers").filter("INVALID").get()
except AuthError as e:
    print(f"Auth failed: {e}")
except NotFoundError as e:
    print(f"Not found: {e}")
except ThrottledError as e:
    print(f"Rate limited, retry after {e.retry_after}s")
except ValidationError as e:
    print(f"Bad request: {e.bc_message}")
    print(f"Correlation ID: {e.correlation_id}")
```

## MCP Server Example

```python
from mcp import Server
from bcli import AsyncBCClient

server = Server("bc-mcp")

@server.tool()
async def query_bc(endpoint: str, filter: str = None, top: int = 10):
    """Query Business Central."""
    async with AsyncBCClient(profile="production") as client:
        query = client.query(endpoint).top(top)
        if filter:
            query.filter(filter)
        return await query.get()
```

## Configuration

The SDK uses the same config as the CLI (`~/.config/bcli/config.toml`). You can also pass config programmatically:

```python
from bcli import BCClient, BCConfig
from bcli.config import BCProfile, BCDefaults

config = BCConfig(
    defaults=BCDefaults(profile="custom"),
    profiles={
        "custom": BCProfile(
            tenant_id="...",
            environment="Production",
            company_id="...",
            client_id="...",
            client_secret_env="MY_SECRET",
        )
    }
)

client = BCClient(config=config)
```
