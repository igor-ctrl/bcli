# bcli

Python SDK and CLI for Microsoft Dynamics 365 Business Central APIs.

## Install

```bash
pip install bcapi
```

## Quick Start

```bash
# Configure
bcli config init

# Query standard v2.0 APIs immediately
bcli get customers --top 5
bcli get items --filter "displayName eq 'ATHENS Desk'" --format json

# Import custom APIs from Postman collection
bcli registry import --from-postman ./my_collection.json

# Query custom endpoints
bcli get engineOverviews --top 3 --format table
```

## SDK Usage

```python
from bcapi import BCClient

client = BCClient(profile="production")
records = client.query("customers").top(5).get()
```
