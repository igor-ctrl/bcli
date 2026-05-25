## OData $filter cheatsheet (bcli starter pack)

Business Central exposes OData v4 filters. The most common shapes:

### Equality + comparison

```bash
# String equality — single quotes around the value
bcli get vendors --filter "no eq 'V00010'"
bcli get customers --filter "displayName eq 'Adatum Corporation'"

# Numeric / boolean
bcli get items --filter "unitPrice gt 100"
bcli get vendors --filter "blocked eq false"

# Date range — ISO-8601 unquoted, ge/le inclusive
bcli get salesInvoices --filter \
  "postingDate ge 2026-01-01 and postingDate le 2026-01-31"
```

### Substring, list, and grouping

```bash
# Substring
bcli get vendors --filter "contains(displayName, 'Air')"
bcli get customers --filter "startswith(displayName, 'Adatum')"

# Multiple conditions
bcli get items --filter "unitPrice gt 50 and inventory gt 0"

# Grouping with `and` / `or` / `not`
bcli get salesInvoices --filter \
  "(status eq 'Open' or status eq 'Draft') and customerId eq 'GUID'"
```

### Choosing fields + ordering + paging

```bash
bcli get vendors \
  --filter "blocked eq false" \
  --select "no,displayName,balance,currencyCode" \
  --orderby "balance desc" \
  --top 25
```

### Filter validation (catches typos before HTTP)

bcli runs a pre-flight check on `--filter` when the entity's fields
are known. Misspelled field names get a "Did you mean: …?" suggestion
*before* a single HTTP request is made. If your filter still fails,
run `bcli endpoint fields <name>` first to populate the field list.
