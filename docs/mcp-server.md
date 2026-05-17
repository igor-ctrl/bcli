# bcli-mcp — MCP server for Claude Desktop and other MCP clients

> **Preview / experimental** in 0.2.x. Tool surface and JSON shapes may shift before we cut 1.0.

`bcli-mcp` is an [MCP (Model Context Protocol)](https://modelcontextprotocol.io)
server that lets MCP-aware clients drive bcli. The intended caller is Claude
Desktop, but it also works with the official [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
and any other client that speaks the spec.

The server generates its tool list **dynamically** by subprocessing
`bcli describe --format json` once on startup, and delegates every call to the
bcli CLI as a subprocess. Profile resolution, auth, retry, telemetry, and the
read-only `disable_writes` gate are inherited from the CLI for free. New CLI
commands light up automatically as MCP tools; deprecated ones disappear.

Read commands return parsed stdout JSON. Mutating commands (`bcli_post`,
`bcli_patch`, `bcli_delete`, `bcli_attach_upload`, `bcli_batch_run`) pass
`--result-out <tmp>` and return the AIP §Phase 2 result envelope content as
the tool result. A `status="failed"` envelope surfaces as an MCP `ToolError`
with the BC correlation id quoted so the agent can cite it.

## Install

```bash
pip install "bc-cli[mcp]"
# or, with uv (recommended)
uv tool install "bc-cli[mcp]"
```

The `mcp` extra brings in the MCP package itself. The CLI runtime ships with
the base `bc-cli` install because the server subprocesses `bcli`.

After install, the `bcli-mcp` console script is on PATH:

```bash
bcli-mcp --help     # FastMCP doesn't currently print help — the script
                    # waits on stdio for an MCP client to connect.
```

## Configure Claude Desktop

Add a `mcpServers` entry to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "bcli": {
      "command": "bcli-mcp",
      "env": {
        "BCLI_PROFILE": "production"
      }
    }
  }
}
```

Restart Claude Desktop. The server registers one tool per command in
`bcli describe`'s output — typically ~20 tools, one per read/mutating verb in
the CLI. Use the MCP Inspector to enumerate them, or run
`bcli describe --format json` directly to preview what'll appear.

Renames from the pre-Phase-5 surface: the old hand-written `query`,
`list_endpoints`, `describe_endpoint`, and `list_companies` are now
`bcli_get`, `bcli_endpoint_list`, `bcli_endpoint_info`, and `bcli_company_list`
respectively (matching the CLI subcommand paths).

If `bcli-mcp` isn't on Claude Desktop's PATH (uv tool install paths can be
tricky), use the full path:

```json
{
  "mcpServers": {
    "bcli": {
      "command": "/Users/you/.local/bin/bcli-mcp",
      "env": { "BCLI_PROFILE": "production" }
    }
  }
}
```

## Tool surface

The tool list is generated from `bcli describe --format json` on startup, so
the canonical reference is the describe output for your install. The most
useful subset:

| Tool | What it does | Notes |
|------|--------------|-------|
| `bcli_get` | Run an OData query against an entity. | `top` defaults to 50, capped at 1000 (carried from describe's `limits`). Use `select` to keep payloads small. |
| `bcli_endpoint_list` | List entities the active profile can reach. | Honours `disable_standard_api`, `allowed_categories`, `allowed_endpoints`. |
| `bcli_endpoint_info` | Show fields, key, supported ops, and route for one entity. | `fields` is populated only after `bcli endpoint fields <name>` has been run. |
| `bcli_endpoint_fields` | Discover the fields for one entity and persist them to the local registry. | One BC API call; populates the cache for every future call. |
| `bcli_company_list` | Companies on the active environment. | Returns `[{id, name, alias, is_default}]`. |
| `bcli_q` | Run a saved query by name with `${{ params.X }}` substitution. | The "daily questions" surface — hides OData syntax. |
| `bcli_post` / `bcli_patch` / `bcli_delete` | Mutating verbs. | Server passes `--result-out <tmp>` and returns the AIP §Phase 2 result envelope as the tool result. `status="failed"` raises `ToolError` with the BC correlation id. |
| `bcli_attach_upload` | Two-phase document attachment upload. | Same envelope contract as the other mutating verbs. |
| `bcli_batch_run` | Execute a YAML batch file. | Returns an envelope whose `record_id` is the batch ledger run id; pivot to `bcli_describe`'s batch state subcommand for per-step detail. |
| `bcli_describe` | Re-emit the full describe payload. | The MCP server itself uses this on startup; tools can call it to discover what else is exposed. |

`auth login`, `config init`, and other interactive commands (`effects:
["other"]` in describe) are filtered out — those are command-line-only.

## Trust model — why the server resets cwd

`bcli` auto-discovers `.bcli.toml` from the current working directory upward
(see `src/bcli/config/_loader.py`). Claude Desktop launches MCP servers
inheriting whatever cwd it was started from, which could be a directory
containing a stale or hostile project-level config.

`bcli-mcp` mitigates this by `chdir`-ing to `$HOME` at startup, before
constructing the FastMCP server or running any tool. The trusted config
sources are then exactly:

* `~/.config/bcli/config.toml` (global config)
* `BCLI_PROFILE` (env var, set in `claude_desktop_config.json`)

Per-tool calls do not honour a per-request `cwd` argument. The server runs
with a single fixed working directory for its lifetime.

## BC query objects vs entity pages — what to expect from `bcli_get`

Not every endpoint in BC's OData surface is a fully-featured entity. Some
are "query objects" — read-only summary pages exposed via OData (e.g.
`customerSales`, `vendorPurchases`). They behave like entities for `GET`
but Microsoft's runtime drops `$orderby` and `$filter` support on most of
them. A `bcli_get` call against one of these with `orderby=` or `filter=`
will 400 from BC.

How to recognise one: there's no flag in the registry today (it'd require
a hint per endpoint), so the practical signal is the 400 itself. The
recovery pattern that works:

1. `bcli_get(endpoint="customerSales", top=1000)` — pull a bounded page.
2. Sort the result client-side in your reasoning step.
3. Take the top N.

This is what an agent will figure out on its own when the orderby
attempt 400s. It also means **`top` must be large enough to contain the
true top-N** — if there are 5000 customers and you sort the first 1000,
you may miss the actual top customer. For BC tenants with very large
summary pages, fall back to `bcli get …` via Bash with `--all` (which
follows pagination).

Entity pages (most of `bcli_endpoint_list`) support full OData. Use
`bcli_endpoint_info(name)` to see whether `fields_discovered` is `true`
and what `fields` look like; the registry doesn't currently track which
endpoints are query objects vs entities, so you'll learn this empirically.

## Discovering field names

`bcli_endpoint_info(name)` returns `fields: []` and `fields_discovered:
false` if the local registry hasn't probed BC for that entity yet. Two
ways to recover, in order of cost:

1. Cheapest: call `bcli_get(endpoint=name, top=1)` once and read the keys
   off the returned record. No registry mutation, zero cache pollution.
2. One-time: call `bcli_endpoint_fields(name)`. That fetches one record,
   persists the field names to the local registry, and every subsequent
   call (any user, any session) gets `fields_discovered: true` for free.

Pick (1) for one-shot analysis. Pick (2) when the entity is one you'll
revisit a lot — the registry-cached field list also feeds bcli's
filter-validation suggestions in the CLI.

## Token economy — when the MCP wins, when it loses

Pairing an MCP server with a CLI tool is empirically token-favorable for
**bounded, schema-stable** responses. The OSS server ships with two
guard-rails baked in:

* `bcli_get.top` defaults to 50 (max 1000) — the safety bound is carried from
  `bcli describe`'s `limits` field, so an unbounded request can't pull a whole
  table into context.
* Tool descriptions come straight from the CLI's docstrings. They're short by
  construction — the schema-payload Claude sees is small.

It is **not** universally a token win. For browse-style "show me everything"
workflows, falling back to `bcli get <entity> --format markdown` via Bash is
often cheaper because Claude renders compact markdown tables directly without
the JSON serialization overhead. Use the right tool for the shape of the
question.

## Future: a Beautech-specific MCP (separate package)

The OSS `bcli-mcp` is intentionally generic. Domain-specific MCPs that combine
BC OData with cross-system data (legal docs, market intel, fleet analytics)
should live in their own packages, installed alongside this one.

The boundary rule:

* **OSS owns** generic BC transport, query construction, registry, OData
  escaping, auth, retry, telemetry plumbing.
* **Private package owns** domain tool composition only — tools like
  `engine_lookup`, `lease_amendments_for`, `vendor_analytics` that combine
  multiple BC queries with cross-system data into one named operation.
* The private MCP is a *consumer* of `bcli` (subprocess or
  `from bcli import AsyncBCClient`), never a layer that re-implements
  transport.

Two separate MCP processes, two `mcpServers` blocks in
`claude_desktop_config.json`. Failure isolation: a private-package bug can't
crash the public MCP, and an OSS install never needs to skip optional
private-package imports.

If exposing mutating tools from a private MCP, the package must either
subprocess `bcli post / patch / delete` (so the CLI's `disable_writes` gate
applies) or reimplement that gate locally — the SDK alone does not enforce
it. See `src/bcli_cli/_safety.py` for the canonical helper.
