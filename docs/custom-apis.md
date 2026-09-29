# Custom APIs

bcli ships with the 79 standard Microsoft Business Central API v2.0 entities
(`customers`, `vendors`, `salesInvoices`, ...). If your extension publishes its
own API pages in AL, register them once and they work exactly like the
standard ones:

```bash
bcli get shipmentTrackings --top 5
bcli endpoint fields shipmentTrackings
```

You never type the publisher, group, or version again. The registry remembers
the route.

## What bcli needs to know

A custom API page is reached at:

```
/api/{APIPublisher}/{APIGroup}/{APIVersion}/companies({id})/{EntitySetName}
```

Those four values come straight from your AL page:

```al
page 50100 "Shipment Tracking API"
{
    PageType = API;
    APIPublisher = 'contoso';
    APIGroup = 'integration';
    APIVersion = 'v1.0';
    EntityName = 'shipmentTracking';
    EntitySetName = 'shipmentTrackings';
    ...
}
```

## Option 1: discover from BC (recommended)

If you can sign in to the environment, let bcli read the route's `$metadata`
and register every entity set it publishes:

```bash
bcli registry import --from-metadata --publisher contoso --group integration --version v1.0
```

This also records each entity's field names, so `--filter` typos get
"did you mean" suggestions. Run it once per route. Imports merge, so a
second route is added next to the first rather than replacing it.

## Option 2: write a short registry file

When you can't reach the environment (CI, a teammate without access yet), or
you only want a few endpoints, list them in YAML or JSON. See
[`examples/custom-apis.yaml`](../examples/custom-apis.yaml):

```yaml
publisher: contoso
group: integration
version: v1.0
endpoints:
  - shipmentTrackings
  - entity_set_name: carrierRates
    description: Carrier rate cards
    supports: [GET, POST, PATCH, DELETE]
```

```bash
bcli registry import --from-file custom-apis.yaml
```

The top-level `publisher` / `group` / `version` apply to every entry; an entry
can override any of them. The long spellings `api_publisher` / `api_group` /
`api_version` are accepted too. An entry that names only part of a route is
rejected with an error instead of silently routing to the standard API.

Optional per-entry keys:

| Key | Default | Purpose |
|---|---|---|
| `description` | `""` | Shown by `bcli endpoint search` / `info` |
| `supports` | `[GET]` | HTTP methods the page allows |
| `category` | the group | Used by `bcli endpoint list --category` and scoped profiles |
| `entity_name` | singular of the set name | Informational |
| `caution` | inferred | `low` / `medium` / `high`; names containing verbs like `post`, `release`, `void` default to `high` |
| `field_names` | `[]` | Known fields for filter validation (learned automatically by `bcli endpoint fields`) |

`--from-file` also accepts a Postman v2.1 collection. Every request URL that
follows the custom API pattern above becomes an endpoint.

## Share your registry

Export what a profile knows and commit it next to your AL extension, or hand
it to a teammate:

```bash
bcli registry export -o custom-apis.json
# on another machine
bcli registry import --from-file custom-apis.json
```

For a team, the same file can ship as a registry preset inside a
[pack](../packs/), or inside a signed team bundle
(`bcli config refresh`), so new users get your endpoints on install.

## Manage registries

```bash
bcli registry list                          # which profiles have custom endpoints
bcli endpoint list --custom                 # just your endpoints
bcli endpoint info shipmentTrackings        # route, methods, caution, known fields
bcli test endpoint shipmentTrackings        # fetch one record to confirm access
```

Registries are per profile and live at `~/.config/bcli/registries/<profile>.json`.
Pass `--profile` to import into a profile other than the active one. Pass
`--replace` to drop previously imported endpoints instead of merging.
Endpoints installed by a pack are kept, because the pack still owns them.

## How route resolution works

When you run `bcli get <name>`:

1. **Custom registry.** If `<name>` is registered for the profile, its route is used.
2. **Standard v2.0.** Otherwise the built-in standard registry routes to `/api/v2.0/`.
3. **Not found.** bcli suggests close matches and tells you how to register the endpoint.

A custom entry with the same name as a standard entity wins, which lets you
point `customers` at your own extended API page if you have one.
