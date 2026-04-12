# bcapi

Python SDK and CLI for Microsoft Dynamics 365 Business Central APIs.

## Install

```bash
pip install bcapi
```

## Quick Start

```bash
# Configure
bcapi config init

# Query standard v2.0 APIs immediately
bcapi get customers --top 5
bcapi get items --filter "displayName eq 'ATHENS Desk'" --format json

# Import custom APIs from Postman collection
bcapi registry import --from-postman ./my_collection.json

# Query custom endpoints
bcapi get engineOverviews --top 3 --format table
```

## SDK Usage

```python
from bcapi import BCClient

client = BCClient(profile="production")
records = client.query("customers").top(5).get()
```
