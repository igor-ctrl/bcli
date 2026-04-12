# Contributing

## Development Setup

```bash
git clone https://github.com/igor-ctrl/bc-cli.git
cd bc-cli
uv venv
uv pip install -e ".[dev]"
```

## Run Tests

```bash
uv run pytest tests/ -v
```

## Lint

```bash
uv run ruff check src/
```

## Project Structure

```
src/
├── bcapi/           # Python SDK (importable library)
│   ├── auth/        # MSAL auth (client credentials, device code, token cache)
│   ├── client/      # HTTP client (async-first, sync wrapper, transport)
│   ├── config/      # TOML config (model, loader, defaults)
│   ├── odata/       # OData query builder, pagination, response wrapper
│   └── registry/    # Endpoint registry, importers, standard_v2.json
├── bcapi_cli/       # CLI layer (Typer)
│   ├── commands/    # One file per command group
│   └── output/      # Formatters (table, json, csv, ndjson)
└── tests/
```

## Architecture Principles

**SDK/CLI split** — The SDK (`bcapi`) is a standalone library with no CLI dependency. The CLI (`bcapi_cli`) is a thin Typer layer that calls the SDK. This lets MCP servers, DAGs, and scripts import `bcapi` directly.

**Async-first** — `AsyncBCClient` is the primary implementation. `BCClient` wraps it for sync contexts. New SDK features should be implemented in `_async.py` first.

**Registry-routed** — The `EndpointRegistry` maps entity names to API routes. Standard v2.0 entities ship in `standard_v2.json`. Custom endpoints are imported per-profile.

**Lazy auth** — Secrets are only resolved when a new token is needed. If a cached token exists, no secret is required.

**Config layering** — Global TOML → project TOML → env vars → CLI flags. The `CLIState` singleton in `_state.py` manages this.

## Adding a New Command

1. Create `src/bcapi_cli/commands/mycommand_cmd.py`
2. Define a Typer `app` and commands
3. Register in `src/bcapi_cli/app.py`

## Adding a New SDK Feature

1. Add the async method to `src/bcapi/client/_async.py`
2. Add the sync wrapper to `src/bcapi/client/_sync.py`
3. Export from `src/bcapi/__init__.py` if it's part of the public API
4. Write tests in `tests/`

## Test Organization

```
tests/
├── test_config/     # Config model and loader
├── test_odata/      # Query builder
├── test_registry/   # Registry lookup and importers
├── test_url/        # URL builder
└── test_cli/        # CLI integration tests
```

## Commit Messages

Follow conventional commits:

```
feat: add new feature
fix: fix a bug
refactor: change structure without new behavior
docs: documentation only
test: add or update tests
chore: build, CI, tooling
```
