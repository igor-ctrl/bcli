# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Install (editable, with dev deps)
uv pip install -e ".[dev]"

# Install globally (puts `bcli` on PATH)
uv tool install -e . --force

# Run tests
uv run pytest tests/ -v

# Run a single test file
uv run pytest tests/test_odata/test_query.py -v

# Run a single test
uv run pytest tests/test_registry/test_registry.py::test_standard_lookup -v

# Lint
uv run ruff check src/

# Run CLI directly from project
uv run bcli --help
```

## Architecture

Two packages in `src/`: **bcli** (SDK library) and **bcli_cli** (Typer CLI). The CLI imports the SDK; the SDK has no dependency on the CLI.

```
bcli_cli (Typer CLI) → bcli (Python SDK) → Business Central APIs
                          ├── auth/       MSAL OAuth2 (client creds + device code)
                          ├── client/     httpx async transport, retry, rate limiting
                          ├── odata/      fluent query builder, pagination, filter validation
                          ├── config/     TOML profiles, layered merge
                          ├── registry/   endpoint metadata → automatic route resolution
                          └── workflow/   ${{ params.X }} resolver (shared by batch + saved queries)
```

### Three-Tier Endpoint Resolution

When `bcli get <entity>` runs, the registry resolves the API route:

1. **Custom registry** (`~/.config/bcli/registries/<profile>.json`) — user-imported endpoints with explicit publisher/group/version routes
2. **Standard v2.0** (`src/bcli/registry/standard_v2.json`) — 79 built-in Microsoft entities, always route to `/api/v2.0/`
3. **Error with suggestions** — fuzzy search proposes similar endpoint names

Custom endpoints are imported via `bcli registry import --from-postman <file.json>` (parses Postman v2.1 URL paths to extract publisher/group/version) or `--from-json` or `--from-metadata`.

A profile with `disable_standard_api = true` strips tier 2 from the registry **and** refuses URL fallback to `/api/v2.0/` when tier 1 misses (`AsyncBCClient._resolve_url`). The `--publisher/--group/--version` override remains the documented escape hatch for power users.

### Async-First with Sync Wrapper

`AsyncBCClient` (`src/bcli/client/_async.py`) is the primary implementation on `httpx.AsyncClient`. `BCClient` (`src/bcli/client/_sync.py`) wraps it — detects if an event loop is running and uses a thread pool if needed, otherwise `asyncio.run()`. CLI commands use `asyncio.run()` directly to call async client methods.

### Config Layered Merge

`load_config()` in `src/bcli/config/_loader.py` merges three layers (later wins):
1. Global: `~/.config/bcli/config.toml`
2. Project: `.bcli.toml` (walks up from CWD)
3. Env vars: `BCLI_PROFILE`, `BCLI_FORMAT`, `BCLI_TIMEOUT`

CLI flags (`--profile`, `--env`, `--company`) override at runtime via `CLIState` in `src/bcli_cli/_state.py`.

### Transport Retry Logic

`BCTransport` (`src/bcli/client/_transport.py`) retries on 429/503/504 with exponential backoff (1s → 2s → 4s, max 3 retries). Honors `Retry-After` header. Parses BC error envelope (`error.message`) and extracts `x-ms-correlation-request-id` for all error types.

### Auth

Two construction modes for `AsyncBCClient`:
1. **Profile-based** — reads TOML config: `AsyncBCClient(profile="production")`
2. **Programmatic** — no config files: `AsyncBCClient(tenant_id=..., client_id=..., client_secret=...)`

Secret resolution order: direct parameter → OS keychain → env var (`BCLI_SECRET`). Secrets are never stored in config. Token cache lives at `~/.config/bcli/tokens.json` with 5-minute expiry buffer. `DeviceCodeAuth` uses MSAL public client for interactive browser auth.

### Sandboxed Domain Profiles

Profiles can run in a sandboxed mode for non-developer domain teams (operations, warehouse, sales, etc.). Set `disable_standard_api = true` and optionally `allowed_categories = [...]` on the profile; the user only sees the endpoints an admin pre-imported. The `bcli config init --scoped --import <file>` wizard bakes this in one shot, defaulting to `device_code` auth so there's no client secret to ship.

Defense-in-depth (each layer catches a different class of mistake):

1. **Curated registry** (`~/.config/bcli/registries/<profile>.json`) — only the endpoints an admin imported are listed. `bcli endpoint list` shows nothing else.
2. **`disable_standard_api = true`** — `_resolve_url` raises `RegistryError` client-side when the entity isn't in the custom registry; no silent `/api/v2.0/` fallback. Tested at `tests/test_client/test_resolve_url.py`.
3. **BC permission set** — server-side filtering on the user's BC account (e.g. row-level security on Vendor by region/department/cost-center). The actual security boundary; the bcli flags are UX guardrails on top.

### Saved Queries

`~/.config/bcli/queries/<profile>.yaml` defines named queries with `${{ params.X }}` substitution, run via `bcli q <name> key=value …`. Hides OData syntax for the daily questions a domain user asks. Reuses the workflow resolver at `src/bcli/workflow/_resolver.py` (the same engine `bcli batch` uses). Schema and starter examples live in `docs/saved-queries.md` and `examples/queries/sample.yaml`.

### Filter Validation

`bcli get … --filter "<expr>"` runs a pre-flight check (`src/bcli/odata/_filter_fields.py`). When the entity has known `field_names` (populated by `$metadata` import or by `bcli endpoint fields`), unrecognised tokens trigger a `Did you mean: …?` suggestion before any HTTP call. No-op for entities without a captured field list, so standard endpoints still fall through to BC's own 400.

`EndpointMetadata.field_names` is the storage. `bcli endpoint fields <entity>` calls a sample record, lists fields, and persists the discovered field names back to the custom registry via `update_endpoint_fields()` in `src/bcli/registry/_importers.py` — so the validator gets smarter the more an entity is touched.

### Read-only Profiles (`disable_writes`)

`BCProfile.disable_writes = true` flags a profile as read-only. The CLI write commands (`bcli post`, `patch`, `delete`, `acme attach`) detect this and run a stderr warning + interactive `yes` confirmation prompt via `bcli_cli/_safety.py:confirm_write_or_exit` before any mutating call. `--yes` / `-y` skips the prompt for scripted use; non-interactive sessions without `--yes` abort. The SDK (`AsyncBCClient`) does NOT enforce this flag — programmatic users get unfiltered access by design.

### Write Safety (SafeContext)

`SafeContext` (`src/bcli/client/_safety.py`) gates write operations:
- Requires explicit `environment` + `company_id` on all writes
- Production writes require `confirm_production=True`
- Domain rules: `finance` = draft-only by default, configurable via `DomainRule`

### Dependency Split

SDK core deps (httpx, msal, pydantic, tomlkit) in base install. CLI deps (typer, rich, pyyaml, keyring) in `[cli]` extra. `pip install bcli` gets SDK only; `pip install "bcli[cli]"` adds CLI.

### Structured Logging

Every HTTP request logs structured JSON to the `bcli.http` logger: method, url, status, retry_count, latency_ms, correlation_id. Transport accepts `log_context` dict for endpoint_tier/environment metadata.

The `--debug` flag (in `src/bcli_cli/app.py`) attaches a stderr handler to the `bcli`, `bcli.http`, `bcli.auth`, and `bcli.client` loggers at DEBUG level — required to see those records, since the loggers have no default handler. Self-rescue path for users when something looks off.

### Telemetry (pluggable backends)

`src/bcli/telemetry/` ships an opt-in usage-telemetry sink. Five event types — `bcli.startup`, `bcli.command`, `bcli.query`, `bcli.auth`, `bcli.error` — are emitted from the obvious code paths (app.py atexit, get_cmd, query_cmd, auth_cmd). Privacy defaults are conservative: tokens/secrets are regex-redacted in `bcli.error.bc_message`, filter text and signed-in UPN are dropped unless `capture_filter_text`/`capture_user_upn` flip them on.

Backends are pluggable via `[telemetry] backend`:

- `"null"` (default) — `NullSink`, zero overhead.
- `"console"` — `ConsoleSink`, JSON to stderr (dev/debug aid).
- `"azure_monitor"` — `AzureMonitorSink`, Azure App Insights via the optional `[telemetry]` extra (`azure-monitor-opentelemetry`).
- `"my_pkg.module:MySink"` — any importable class implementing the `TelemetrySink` Protocol (must expose `is_active`, `emit`, `flush`, and a `from_config` classmethod). Useful for AWS CloudWatch, Datadog, Honeycomb, an internal HTTP webhook, etc.

`get_sink(config)` (`src/bcli/telemetry/_factory.py`) does the dispatch and falls back to `NullSink` on any load/import/from_config failure with a one-shot warning — telemetry never crashes the CLI.

## Key Paths

| Path | Purpose |
|------|---------|
| `src/bcli/__init__.py` | Public SDK API: BCClient, AsyncBCClient, SafeContext, Query, EndpointRegistry |
| `src/bcli/client/_transport.py` | HTTP layer — retry, auth injection, BC error parsing, structured logging |
| `src/bcli/client/_async.py` | AsyncBCClient — profile-based + programmatic auth, `_resolve_url()` |
| `src/bcli/client/_safety.py` | SafeContext — write safety gate with configurable domain rules |
| `src/bcli/registry/_registry.py` | EndpointRegistry — three-tier lookup, fuzzy search |
| `src/bcli/registry/_importers.py` | Postman/JSON/$metadata parsers |
| `src/bcli/registry/_schema.py` | EndpointMetadata with domain tag (standard/finance/technical) |
| `src/bcli/registry/standard_v2.json` | Built-in standard v2.0 entity definitions |
| `src/bcli/odata/_query.py` | Fluent query builder (filter/select/expand/orderby/top/skip) |
| `src/bcli/odata/_filter_fields.py` | Pre-flight `--filter` validator: extracts field tokens, suggests close matches |
| `src/bcli/workflow/_resolver.py` | `${{ params.X }}` / `${{ steps.X.field }}` resolver shared by `bcli batch` and `bcli q` |
| `src/bcli/config/_loader.py` | Config loading with `_deep_merge()`, tomlkit serialization |
| `src/bcli_cli/app.py` | Typer root — registers all command groups, global options callback, `--debug` logging wiring |
| `src/bcli_cli/_state.py` | CLIState singleton — lazy config/registry, per-command overrides |
| `src/bcli_cli/commands/query_cmd.py` | `bcli q` saved-query runtime — loads YAML, expands params, dispatches GET |
| `src/bcli_cli/commands/config_cmd.py` | `bcli config init` wizard — supports `--scoped --import` for domain teams |

## CLI Command Name

The CLI binary is `bcli` (set in `pyproject.toml` `[project.scripts]`). The Python package is `bcli` (import name `bcli`). User-facing messages should reference `bcli`.

## GBrain Configuration (configured by /setup-gbrain)
- Engine: pglite
- Config file: ~/.gbrain/config.json (mode 0600)
- Setup date: 2026-04-24
- MCP registered: yes
- Memory sync: full
- Current repo policy: read-write

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. The
skill has multi-step workflows, checklists, and quality gates that produce better
results than an ad-hoc answer. When in doubt, invoke the skill. A false positive is
cheaper than a false negative.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke /office-hours
- Strategy, scope, "think bigger", "what should we build" → invoke /plan-ceo-review
- Architecture, "does this design make sense" → invoke /plan-eng-review
- Design system, brand, "how should this look" → invoke /design-consultation
- Design review of a plan → invoke /plan-design-review
- Developer experience of a plan → invoke /plan-devex-review
- "Review everything", full review pipeline → invoke /autoplan
- Bugs, errors, "why is this broken", "wtf", "this doesn't work" → invoke /investigate
- Test the site, find bugs, "does this work" → invoke /qa (or /qa-only for report only)
- Code review, check the diff, "look at my changes" → invoke /review
- Visual polish, design audit, "this looks off" → invoke /design-review
- Developer experience audit, try onboarding → invoke /devex-review
- Ship, deploy, create a PR, "send it" → invoke /ship
- Merge + deploy + verify → invoke /land-and-deploy
- Configure deployment → invoke /setup-deploy
- Post-deploy monitoring → invoke /canary
- Update docs after shipping → invoke /document-release
- Weekly retro, "how'd we do" → invoke /retro
- Second opinion, codex review → invoke /codex
- Safety mode, careful mode, lock it down → invoke /careful or /guard
- Restrict edits to a directory → invoke /freeze or /unfreeze
- Upgrade gstack → invoke /gstack-upgrade
- Save progress, "save my work" → invoke /context-save
- Resume, restore, "where was I" → invoke /context-restore
- Security audit, OWASP, "is this secure" → invoke /cso
- Make a PDF, document, publication → invoke /make-pdf
- Launch real browser for QA → invoke /open-gstack-browser
- Import cookies for authenticated testing → invoke /setup-browser-cookies
- Performance regression, page speed, benchmarks → invoke /benchmark
- Review what gstack has learned → invoke /learn
- Tune question sensitivity → invoke /plan-tune
- Code quality dashboard → invoke /health
