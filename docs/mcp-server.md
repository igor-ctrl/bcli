# bcli-mcp — MCP server for Claude Desktop and other MCP clients

> **Preview / experimental** in 0.2.x. Tool surface and JSON shapes may shift before we cut 1.0.

`bcli-mcp` is an [MCP (Model Context Protocol)](https://modelcontextprotocol.io)
server that lets MCP-aware clients drive bcli. The intended caller is Claude
Desktop, but it also works with the official [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
and any other client that speaks the spec.

The server is deliberately small (4 read-only tools) and delegates every call
to the bcli CLI as a subprocess. Profile resolution, auth, retry, telemetry,
and the read-only `disable_writes` gate are inherited from the CLI for free.

## Install

```bash
pip install "bc-cli[mcp]"
# or, with uv (recommended)
uv tool install "bc-cli[mcp]"
```

The `mcp` extra brings in the `cli` extras (typer/rich/pyyaml/keyring/workos)
plus the `mcp` package itself, since the server subprocesses bcli.

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

Restart Claude Desktop. You should see four tools register: `query`,
`list_endpoints`, `describe_endpoint`, `list_companies`.

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

| Tool | What it does | Notes |
|------|--------------|-------|
| `query` | Run an OData query against an entity. | `top` defaults to 50, capped at 1000. Use `select` to keep payloads small. |
| `list_endpoints` | List entities the active profile can reach. | Honours `disable_standard_api`, `allowed_categories`, `allowed_endpoints`. |
| `describe_endpoint` | Show fields, key, supported ops, and route for one entity. | `fields` is populated only after `bcli endpoint fields <name>` has been run. |
| `list_companies` | Companies on the active environment. | Returns `[{id, name, alias, is_default}]`. |

Mutating commands (`post` / `patch` / `delete`), file uploads, batch runs, and
admin/setup flows are deliberately not exposed. Claude can always fall back to
`Bash` + `bcli` directly when those are needed — and that path trips the
existing `disable_writes` confirmation prompt.

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

## Token economy — when the MCP wins, when it loses

Pairing an MCP server with a CLI tool is empirically token-favorable for
**bounded, schema-stable** responses. The OSS server ships with two
guard-rails baked in:

* `query.top` defaults to 50 (max 1000) — an unbounded request can't pull a
  whole table into context.
* Tool docstrings are short. The schema-payload Claude sees is small.

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
