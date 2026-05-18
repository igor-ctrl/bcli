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

The endpoint **registry** does the same job for the API route. When you
write `bcli get fixedAssets`, the registry already knows the
publisher / group / version for `fixedAssets` and builds the URL for
you. You do **not** need `--publisher … --group … --version …` — those
are escape hatches for the rare case where an admin hasn't imported the
endpoint yet, and they're hidden from `--help` for that reason. If
`bcli get <name>` errors with `RegistryError`, the fix is to import the
endpoint into the registry, not to pass override flags.

---

## Don't write this — minimal command, please

The single biggest tell that an agent is over-pattern-matching:
redundant flags that the profile + registry already supply.

```bash
# ❌ Don't write this:
bcli -c LLC get fixedAssets --publisher beautech --group finance --version v1.5 --all -f json

# ✅ Write this:
bcli -c LLC get fixedAssets
```

Why each flag was wrong:

- `--publisher beautech --group finance --version v1.5` — the
  registry resolves these automatically. Only pass them if the
  endpoint isn't in the registry (and even then, prefer importing it).
- `--all` — pulls **every** page. Most asks need `--top 5` or no
  pagination flag at all. Use `--all` only when the user explicitly
  asks for a full export.
- `-f json` — bcli already auto-detects agents (`CLAUDECODE=1`,
  `BCLI_AGENT=1`, or non-TTY stdout) and emits markdown. Pass `-f
  json` only when you actually need to feed the result into `jq` or
  another tool call.

Rule of thumb: **start with the shortest command that names the
action** (`bcli get fixedAssets`), then add flags one at a time only
when the user's question demands them.

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

## Mutation result envelope — read this, not stdout

For real writes (not dry-run), pass `--result-out PATH` (or
`--result-fd N` on Unix harnesses) and parse the JSON envelope written
there. The envelope is the canonical record of what happened — stdout
is for human consumers; agents shouldn't scrape it.

```bash
bcli --profile p post vendors --data '{"...": "..."}' --result-out /tmp/r.json
# then jq < /tmp/r.json
```

The envelope is an 18-field JSON object: `invocation_id`,
`tool_version`, `profile`, `environment`, `company`, `method`,
`endpoint`, `resolved_url`, `record_id`, `dry_run`, `status`
(`succeeded` / `failed`), `exit_code`, `bc_correlation_id`,
`telemetry_event_id`, `audit_log_offset`, `started_at`,
`duration_ms`, plus `version` of the envelope schema. Atomic write
(tmp + `os.replace` + `fsync`) — the file appears whole or not at all.

**Failed envelopes** keep the same shape with `status="failed"`,
`exit_code` per the taxonomy below, and `bc_correlation_id` set when
BC returned one. Use it to diagnose without scraping a Python
traceback off stderr.

For `bcli batch run`, the envelope's `record_id` IS the ledger run id.
Pivot from there to `bcli batch state <run-id>` for per-step detail.

## Batch operation state

`bcli batch run` writes a durable SQLite ledger that survives SIGKILL.
Three new commands let you inspect and undo runs:

```bash
bcli batch list --format json                  # recent runs, newest first
bcli batch state <run-id> --format json        # per-step detail for one run
bcli batch rollback <run-id> --dry-run         # preview undo
bcli batch rollback <run-id>                   # apply undo (POST→DELETE only)
```

`bcli batch list` filters with `--state STATE` (`completed`, `failed`,
`partially_committed`, `rolled_back`, `running`, `cancelled`). Use
`partially_committed` to find runs that died mid-way — those are where
ledger-based recovery is worth the cost.

Rollback issues `DELETE` for committed POSTs only. PATCH and DELETE
steps are marked `rollback_skipped` because there's no clean inverse
without a pre-image snapshot. `disable_writes` profiles refuse rollback
outright (no `--yes` bypass) — that's by design.

## Exit code taxonomy

Don't treat all non-zero as "generic error." The taxonomy is
documented in `bcli describe`'s `exit_codes` field:

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | generic crash / unhandled exception |
| 2 | usage error (bad flag, missing arg) |
| 3 | auth (token expired, login required) |
| 4 | not found (endpoint, record, profile) |
| 5 | validation (filter, param schema) |
| 6 | remote 4xx (BC rejected the request) |
| 7 | remote 5xx (BC server error) |
| 8 | policy refusal (`disable_writes` triggered without `--yes`) |

Key off the specific code. Exit `8` means "the profile is read-only";
prompting for `--yes` and retrying is the right move. Exit `3` means
"run `bcli auth login --profile X`." Exit `1` is the only one that
warrants "report to user and stop."

## Idempotency keys for retries

For mutations you might retry, pass `--idempotency-key K`. The IETF
`Idempotency-Key` HTTP header goes out on the first call so any
gateway-level dedup applies; subsequent retries within the same `bcli
batch run` short-circuit through the ledger (no second HTTP, no
duplicate row).

```bash
KEY=$(uuidgen)
bcli post vendors --data '{"...":"..."}' --idempotency-key "$KEY" --result-out r.json
# If you need to retry, reuse the same KEY — same-run replay is safe.
```

Inside a batch YAML, declare per-step:

```yaml
steps:
  - name: create_vendor
    action: post
    endpoint: vendors
    idempotency_key: "${{ params.vendor_no }}-2026Q2"
    body: { ... }
```

Cross-run replay is deferred (would mean scanning every ledger DB); the
header is sent on every retry so gateway-level dedup remains in play.

## Dry-run before writes

Before any `post` / `patch` / `delete` / `attach upload`, run with `--dry-run`
and `-f json` to get a structured preview the user can sanity-check:

```bash
bcli --dry-run -f json post customers --data '{"displayName": "Test"}'
```

The response is a JSON envelope:

```json
{
  "dry_run": true,
  "method": "POST",
  "endpoint": "customers",
  "resolved_url": "https://.../api/v2.0/companies(<id>)/customers",
  "profile": "dev",
  "environment": "Sandbox",
  "company_id": "<id>",
  "body": {"displayName": "Test"}
}
```

`PATCH` and `DELETE` envelopes also include `record_id`. The shape is stable —
field names won't change. Use it to:

* Show the user the resolved URL + body before they approve a real write.
* Catch typos in the endpoint name before any HTTP call goes out.
* Verify the right environment / company is targeted.

## Caution levels for write endpoints

Every endpoint exposes a `caution` level (`low` / `medium` / `high`) via
`bcli endpoint info` and the `list_endpoints` MCP tool. Endpoints whose name
contains a mutation verb (`post`, `release`, `cancel`, `void`, `reverse`,
`apply`, `unapply`) are flagged `high` automatically. Treat `high` as: "do
not write without explicit user confirmation, even if the user previously
authorised a similar action." Examples:

* `customers` → `low` (CRUD on a master-data record)
* `salesInvoicePost` → `high` (irreversibly posts an invoice)
* `customerLedgerEntryApply` → `high` (modifies posted ledger state)

## Audit log location

When the user has `[audit] enabled = true` in `~/.config/bcli/config.toml`,
every write you trigger appends a JSON line to
`~/.config/bcli/audit/<profile>.jsonl` (or whatever the user configured).
Each entry includes `outcome` (`completed` / `failed` / `dry_run`),
`correlation_id` (BC's `x-ms-correlation-request-id`), `latency_ms`, and the
redacted request body. Useful for:

* Showing the user "here's what just happened" after a multi-step task.
* Grepping for the BC correlation ID when debugging a 500 the user reported.
* Reconciling intent (`dry_run` entries) against actual writes.

You don't need to enable the log yourself — it's the user's choice. If they
ask "did that POST go through?" the audit log is the canonical answer when it's
on, and the CLI exit code is the answer when it's off.

## When you have an MCP server

If the user has mounted `bcli-mcp` (see [`docs/mcp-server.md`](docs/mcp-server.md)),
prefer those tools — they collapse discovery + query into single calls
with structured results. As of 0.4.0 the MCP server generates 23 tools
dynamically from `bcli describe`, including five new mutating verbs
(`bcli_post`, `bcli_patch`, `bcli_delete`, `bcli_attach_upload`,
`bcli_batch_run`) that internally pass `--result-out` and return the
envelope as the tool result.

**Tool names match the CLI command path** (`bcli_get`,
`bcli_endpoint_list`, `bcli_endpoint_info`, `bcli_endpoint_fields`,
`bcli_company_list`, …). The pre-0.4.0 names (`query`,
`list_endpoints`, `describe_endpoint`, `list_companies`) are gone — see
the migration table in [`docs/mcp-server.md`](docs/mcp-server.md) if
your client config references the old names.

For mutating tools, a `status="failed"` envelope surfaces as MCP
`ToolError` with the BC correlation id quoted in the error message —
you don't need to read the envelope separately for failures.

The CLI recipes above still work fine if the MCP server isn't
available; the MCP tools are an "if you've got them, use them"
optimization.

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
