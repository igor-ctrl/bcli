# Multi-Company Support

Business Central environments often contain multiple companies (entities). bcli lets you assign short aliases to companies and query across all of them in a single command.

## Discover Companies

```bash
bcli company list
```

Output:
```
  #  Alias  Company Name              Company ID
  1  LLC    Acme Power Systems    f99bd320-b400-...  ◄ default
  2  Corp   Acme Corp             a1b2c3d4-e5f6-...
```

## Assign Aliases

Give companies short, memorable names:

```bash
bcli company alias LLC f99bd320-b400-4189-b3c1-c62c05d4e7a5 --name "Acme Power Systems LLC"
bcli company alias Corp a1b2c3d4-e5f6-7890-abcd-ef1234567890 --name "Acme Corp"
```

View all aliases:

```bash
bcli company aliases
```

## Query by Alias

Use `--company` (global flag, before the command) with an alias instead of a GUID:

```bash
bcli -c LLC get customers --top 5
bcli -c Corp get vendors --filter "balance gt 0"
```

## Query All Companies

Use `--company all` to fan out a query across every aliased company. Each record is tagged with `_company` and `_company_name` columns:

```bash
bcli -c all get customers --top 5
```

Output:
```
  Querying customers in LLC...
    → 5 record(s) from LLC
  Querying customers in Corp...
    → 3 record(s) from Corp

  _company  _company_name              displayName      ...
  LLC       Acme Power Systems     Customer A       ...
  LLC       Acme Power Systems     Customer B       ...
  Corp      Acme Corp              Customer X       ...
```

### Export Across All Companies

```bash
# JSON
bcli -c all -f json -q get customers --all > all_customers.json

# CSV
bcli -c all -f csv -q get items --select number,displayName --all > all_items.csv
```

The `_company` column makes it easy to group, filter, or pivot by entity in downstream tools.

## Set Default Company

```bash
# By alias
bcli company use LLC

# By GUID
bcli company use f99bd320-b400-4189-b3c1-c62c05d4e7a5
```

## Config Format

Aliases are stored in the profile section of `~/.config/bcapi/config.toml`:

```toml
[profiles.production.companies.LLC]
id = "f99bd320-b400-4189-b3c1-c62c05d4e7a5"
name = "Acme Power Systems LLC"

[profiles.production.companies.Corp]
id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
name = "Acme Corp"
```
