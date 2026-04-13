"""bcli ai-context — dump LLM-ready usage instructions."""

from __future__ import annotations

import typer

AI_CONTEXT = """\
## bcli — Business Central CLI/SDK

### Command syntax

```bash
# Global flags work before OR after the subcommand:
bcli get customers --top 5 -f json
bcli --format json get customers --top 5

# Both are equivalent. -f is shorthand for --format.
```

### Output formats

| Flag | Use case |
|------|----------|
| `-f table` | Human-readable (default) |
| `-f json` | Pipe to jq or python |
| `-f ndjson` | Streaming / line-by-line processing |
| `-f csv` | Spreadsheet import |
| `-f raw` | Full OData response with @odata metadata |

Machine-readable formats (`json`, `csv`, `ndjson`, `raw`) auto-suppress the context banner.

### OData filter syntax

Strings use **single quotes**. Dates are **unquoted** ISO format.

```bash
# String equality
bcli get customers --filter "displayName eq 'Fabrikam'"

# Numeric comparison
bcli get items --filter "unitPrice gt 100"

# Date range
bcli get salesInvoices --filter "postingDate ge 2026-01-01 and postingDate le 2026-01-31"

# Contains (substring match)
bcli get vendors --filter "contains(displayName, 'Air')"

# Multiple conditions
bcli get items --filter "unitPrice gt 50 and inventory gt 0"

# Select specific fields (reduces payload)
bcli get customers --select displayName,email,balance --top 10

# Order results
bcli get salesInvoices --orderby "postingDate desc" --top 20
```

### Common patterns

```bash
# Get all records (auto-paginate)
bcli get items --all -f ndjson

# Pipe JSON to python
bcli get customers --top 5 -f json | python3 -c "import sys,json; print(json.load(sys.stdin))"

# Count records
bcli get vendors --filter "balance gt 0" --count --top 1

# Discover endpoints
bcli endpoint list                    # All endpoints
bcli endpoint list --custom           # Only custom API endpoints
bcli endpoint search engine           # Fuzzy search
bcli endpoint info customers          # Metadata for one endpoint
bcli endpoint fields customers        # Field names + types from live data
```

### Profiles and context

bcli uses named profiles. Each profile has a tenant, environment, and default company.

```bash
bcli config show                      # Current profile settings
bcli get customers --top 5            # Uses default profile
bcli -p production get customers      # Use a specific profile
bcli -c LLC get vendors               # Override company by alias
```
"""


def ai_context_command() -> None:
    """Print LLM-ready usage instructions for bcli.

    Output is markdown designed to be appended to a project's CLAUDE.md file.
    Run: bcli ai-context >> CLAUDE.md
    """
    print(AI_CONTEXT)
    raise typer.Exit()
