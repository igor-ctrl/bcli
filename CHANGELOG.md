# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-04-12

### Added
- Python SDK (`bcli`) for Microsoft Dynamics 365 Business Central APIs
- Async-first client (`AsyncBCClient`) with sync wrapper (`BCClient`)
- Three-tier endpoint registry: custom → standard v2.0 → fuzzy suggestions
- Fluent OData query builder with filter/select/expand/orderby/top/skip
- TOML config with layered merge (global → project → env vars)
- MSAL OAuth2 auth: client credentials + device code flows
- OS keychain integration for secret storage
- Custom API import from Postman collections, JSON, and $metadata
- CLI (`bcli`) with commands: get, post, patch, delete, config, registry, auth, batch, company, env, endpoint, test
- Multi-company support with aliases
- Multiple output formats: table, JSON, CSV, NDJSON
- HTTP transport with retry on 429/503/504, exponential backoff, Retry-After support
- Structured JSON request logging (`bcli.http` logger)
- Domain tags on endpoint metadata (standard/finance/technical)
- Dependency split: SDK core vs `[cli]` extra
- `tomlkit`-based config serialization (proper TOML round-trip)

### Changed
- Renamed package from `bcapi` to `bcli` across entire codebase
- Environment variables renamed: `BCAPI_*` → `BCLI_*`
- Config directory: `~/.config/bcapi/` → `~/.config/bcli/`
- Project config: `.bcapi.toml` → `.bcli.toml`
- Base error class: `BCAPIError` → `BCLIError`
