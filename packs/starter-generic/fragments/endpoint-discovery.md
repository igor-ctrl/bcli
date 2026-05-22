## Endpoint discovery (bcli starter pack)

Never guess endpoint names. Always discover them:

```bash
# What entities are available on the active profile?
bcli endpoint list

# Search for an entity by fuzzy name match
bcli endpoint search vendor
bcli endpoint search "purchase order"

# Get full metadata (publisher / group / version / fields) as JSON
bcli endpoint info vendors -f json
```

If `endpoint search` returns nothing, the entity is not registered
on the current profile — try a different profile (`bcli --profile X
endpoint search …`) or import a custom endpoint with `bcli registry
import`.

### Field discovery (don't guess column names)

```bash
# Sample one record and list its fields
bcli endpoint fields vendors

# The fields are then persisted into the custom registry, so the
# next `--filter` validation knows them and can suggest close
# matches when you typo a name.
```

### Pattern

1. **Discover** — `bcli endpoint search <noun>`
2. **Inspect** — `bcli endpoint info <name> -f json`
3. **Sample** — `bcli get <name> --top 1 -f json` (or `bcli endpoint
   fields <name>` to record the field names)
4. **Query** — `bcli get <name> --filter "…" --select "id,no,name"`
