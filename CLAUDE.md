# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Install (editable, with dev deps)
uv pip install -e ".[dev]"

# Install globally (puts `bcli` on PATH)
uv tool install -e /Users/igor/Projects/bc-cli --force

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
                          ├── odata/      fluent query builder, pagination
                          ├── config/     TOML profiles, layered merge
                          └── registry/   endpoint metadata → automatic route resolution
```

### Three-Tier Endpoint Resolution

When `bcli get <entity>` runs, the registry resolves the API route:

1. **Custom registry** (`~/.config/bcli/registries/<profile>.json`) — user-imported endpoints with explicit publisher/group/version routes
2. **Standard v2.0** (`src/bcli/registry/standard_v2.json`) — 79 built-in Microsoft entities, always route to `/api/v2.0/`
3. **Error with suggestions** — fuzzy search proposes similar endpoint names

Custom endpoints are imported via `bcli registry import --from-postman <file.json>` (parses Postman v2.1 URL paths to extract publisher/group/version) or `--from-json` or `--from-metadata`.

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

Secrets are never stored in config. The TOML profile stores `client_secret_env = "BCLI_SECRET"` — the env var name. `ClientCredentialsAuth` reads the env var at runtime. Token cache lives at `~/.config/bcli/tokens.json` with 5-minute expiry buffer. `DeviceCodeAuth` uses MSAL public client for interactive browser auth.

## Key Paths

| Path | Purpose |
|------|---------|
| `src/bcli/__init__.py` | Public SDK API: BCClient, AsyncBCClient, Query, EndpointRegistry |
| `src/bcli/client/_transport.py` | HTTP layer — retry, auth injection, BC error parsing |
| `src/bcli/client/_async.py` | AsyncBCClient — `_build_auth()` selects auth provider, `_resolve_url()` does registry lookup |
| `src/bcli/registry/_registry.py` | EndpointRegistry — three-tier lookup, fuzzy search |
| `src/bcli/registry/_importers.py` | Postman/JSON/$metadata parsers |
| `src/bcli/registry/standard_v2.json` | Built-in standard v2.0 entity definitions |
| `src/bcli/odata/_query.py` | Fluent query builder (filter/select/expand/orderby/top/skip) |
| `src/bcli/config/_loader.py` | Config loading with `_deep_merge()` across layers |
| `src/bcli_cli/app.py` | Typer root — registers all command groups, global options callback |
| `src/bcli_cli/_state.py` | CLIState singleton — lazy config/registry, per-command overrides |

## CLI Command Name

The CLI binary is `bcli` (set in `pyproject.toml` `[project.scripts]`). The Python package is `bcli` (import name `bcli`). User-facing messages should reference `bcli`.
