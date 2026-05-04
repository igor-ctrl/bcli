# AGENTS.md — bcli for AI coding agents

This file is for AI coding agents (Claude Code, Cursor, OpenAI Codex,
etc.) driving `bcli` on a user's behalf. If you're a human, you can
read it too, but the much friendlier intro is
[`docs/getting-started.md`](docs/getting-started.md).

The goal here is simple: **get to the right command in 1–2 tool calls,
not 5**. Most of the time agents stumble on `bcli` because they guess
endpoint names, guess field names, or pass redundant flags. The recipes
below short-circuit those guesses.

---

## Profiles do most of the work — don't override them

A profile (`my-profile`) already encodes:

- `tenant_id`, `client_id` (auth)
- `environment` (e.g. `Production`, `Sandbox`)
- `company_id` (default company)
- `auth_method` (`browser`, `device_code`, `client_credentials`, …)

So `bcli --profile my-profile get vendors --top 5` is enough. You do
**not** need to also pass `-e Production` — the profile already knows.
Use `-e` / `--env` only when the user explicitly asks you to hit a
*different* environment than the profile's default.

Same for `--company` / `-c`: only pass it when switching off the
profile's default company.

---

## Endpoint discovery — don't guess names

The custom registry an organization installs may be a curated subset of
BC's catalog. Names are case-sensitive and not always plural-of-the-
obvious-singular (e.g. `preservationStatuses`, not `preservationStatus`).
The recipe:

```bash
bcli --profile my-profile endpoint search <fuzzy-term>
bcli --profile my-profile endpoint info <name> -f json
bcli --profile my-profile endpoint list -f json    # if you need everything
```

`endpoint search` does fuzzy matching against name + description.
`endpoint info` returns structured metadata (route, key field,
operations, cached field names if any). `endpoint list -f json` is the
machine-parseable enumeration.

**Avoid `endpoint list` without `-f json` from an agent**: the table
format truncates wide columns, especially on narrow terminals. The
markdown / json formats render every column in full.

---

## Field discovery — don't guess fields either

BC custom-API field names sometimes look nothing like the column you'd
expect (`serialNo` rather than `serialNumber`, `no` rather than
`number`). Don't pass `--filter "<guess> eq 'X'"` and hope for the
best — that's 1–2 wasted tool calls per guess.

The canonical command:

```bash
bcli --profile my-profile endpoint fields <name>
```

It fetches one record from the endpoint, prints every field name with
its inferred type and a sample value, and persists those names to the
custom registry so subsequent `--filter` validation can suggest the
right field when you mistype. One API call up front saves three round
trips later.

---

## Output formats — pick on purpose

| Format | When |
|---|---|
| `markdown` | Default for AI agents (Claude Code, etc.) and non-TTY stdout. Renders every column, no ANSI escapes. |
| `json` | Programmatic parsing with `jq`. Use when you need to feed the result back into another tool call. |
| `table` | Interactive humans on a real terminal. Avoid from agents — rich truncates wide columns. |
| `csv` / `ndjson` | Bulk export, downstream pipelines. |
| `raw` | Untransformed BC payload (debug only). |

Agent auto-detection: if `CLAUDECODE=1` or `BCLI_AGENT=1` is set, or
stdout isn't a TTY, `bcli` already defaults to `markdown` without you
asking. Same on classic Windows PowerShell (where rich's box-drawing
otherwise renders as `�` mojibake).

---

## Common errors — what they mean and what to run next

### `RegistryError: Endpoint 'X' is not in this profile's custom registry`

The profile has `disable_standard_api = true` and `X` isn't in the
curated list. The error message itself now includes a `Did you mean:`
suggestion when the name is close to a known one. To explore further:

```bash
bcli --profile my-profile endpoint search <part-of-name>
bcli --profile my-profile endpoint list -f json
```

### `HTTP 400 Bad Request: Could not find a property named 'X' on type ...`

Field name doesn't exist on this entity. The error message now includes
a `Hint: bcli endpoint fields <endpoint>` line — run that, look at the
real field names, retry the query.

### `HTTP 403 Forbidden`

The user's BC permission set denies this endpoint or row. This is a
server-side decision and there's nothing client-side to retry. Tell the
user, don't loop.

---

## When you have an MCP server

If the user has mounted `bcli-mcp` (see [`docs/mcp-server.md`](docs/mcp-server.md)),
prefer those tools — they collapse discovery + query into single calls
with structured results:

- `list_endpoints()` — full registry as JSON
- `describe_endpoint(name, discover_fields=True)` — metadata + field
  discovery in one call
- `query(endpoint, filter, ...)` — the same as `bcli get`, but typed

The CLI recipes above still work fine if the MCP server isn't
available; this is purely an "if you've got it, use it" optimization.

---

## Quick decision flow

```
User asks: "find X for entity Y"
  │
  ├─ Do you know the endpoint name? ──── No ──→ bcli endpoint search Y
  │           │
  │          Yes
  │           │
  ├─ Do you know the field names? ──── No ──→ bcli endpoint fields <name>
  │           │
  │          Yes
  │           │
  └─ bcli --profile <p> get <name> --filter "<field> eq '<value>'" -f json
```

Three tool calls in the worst case (search → fields → get), one in the
best case (just get). Every other shape is a guess.
