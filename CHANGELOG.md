# Changelog

All notable changes to this project are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] — 2026-04-29

OSS-readiness polish. No SDK behaviour changes; one CLI rename and a
README/docstring cleanup pass.

### Changed

- **Renamed CLI command** `bcli acme` → `bcli attach`. The two
  subcommands renamed alongside it: `bcli acme attach` →
  `bcli attach upload`, and `bcli acme test-attach` →
  `bcli attach test`. Functionality is unchanged. The old name was a
  customer-name leftover; the workflow is generic to any Business
  Central tenant.
- Genericised customer-named example values in
  `docs/authentication.md`, `examples/attach-purchase-invoice-pdf.yaml`,
  and SDK docstrings.

### Fixed

- README license badge said Apache 2.0 but the License section said
  MIT. Now consistent: Apache 2.0 throughout (matches the actual
  `LICENSE` file and `pyproject.toml`).

## [0.1.0] — 2026-04-29

First public release on PyPI as **`bc-cli`**.

> **Note on the package name.** The PyPI distribution name is `bc-cli`,
> not `bcli` — the latter is squatted by an unrelated 2018-era
> "EC2 Cluster Creator" package. The Python import name (`import bcli`)
> and the CLI binary on PATH (`bcli`) are unaffected.

### Added

- **SDK** — `BCClient` (sync) and `AsyncBCClient` (async) for Microsoft
  Dynamics 365 Business Central. Two construction modes: profile-based
  (TOML config files) and programmatic (pass auth params directly).
- **CLI** — Typer-based `bcli` command with subcommands for query
  (`get`), write (`post`/`patch`/`delete`/`attach`), config, auth,
  registry import, batch operations, saved queries (`q`), and ETL
  pipelines.
- **Three-tier endpoint resolution.** Custom registry → standard v2.0 →
  fuzzy-match suggestion. Custom APIs imported from Postman v2.1
  collections, raw JSON, or live `$metadata`.
- **Auth** — Client-credentials, device-code, browser (PKCE), and
  WorkOS AuthKit (role → BC client_id mapping). Token cache with 5-min
  expiry buffer; OS keychain integration via `keyring`.
- **Write safety** — `SafeContext` gate enforces explicit `environment`
  + `company_id` on writes; production writes require
  `confirm_production=True`. CLI's `disable_writes` profile flag adds
  an interactive confirmation prompt before any mutating call.
- **Saved queries** (`bcli q`) — Hide OData syntax behind named,
  parameterised aliases stored per-profile. Per-param `type`,
  `pattern`, `min`/`max`, and `enum` validation runs locally before any
  HTTP call. String parameters interpolated into `filter:` are escaped
  using OData v4 single-quote rules so an injection-shaped value cannot
  break the literal.
- **ETL pipeline** — Built-in [dlt](https://dlthub.com) source for
  incremental backup. Polaris REST catalog integration for Iceberg
  snapshots. Generic + bcli-bridge layers.
- **Telemetry** — Pluggable backend with `null`, `console`,
  `azure_monitor`, and arbitrary `module:Class` sinks. Privacy-first
  defaults: token redaction in error messages, opt-in capture of filter
  text and signed-in UPN. Every event carries `version`, `os`,
  `os_release`, `arch`, `python_version`, and a stable per-laptop
  `install_id` for downstream slicing.
- **Structured logging** — JSON request logs to the `bcli.http` logger
  with method/url/status/retry-count/latency/correlation-id.

### Security

The 0.1.0 release went through two independent security review passes
before publish. Findings addressed:

- Pinned wheel-version installer scripts; scrubbed registry `supports`
  arrays to `["GET"]` for read-only profiles; removed sensitive field
  metadata from public artefacts shipped by the bootstrap installer.
- **Critical:** Project-level `.bcli.toml` files cannot override
  `[telemetry] backend`. Closes an arbitrary-Python-import RCE on
  `bcli` invocation in any directory containing a malicious
  `.bcli.toml`.
- **High:** Token caches and identity caches written with `0o600`
  perms via atomic write; parent dir tightened to `0o700`; one-shot
  warning on insecure existing perms.
- **High:** WorkOS role cache now expires after 1 hour; revoked roles
  no longer keep mapping to privileged BC apps indefinitely.
- **Medium:** Absolute-URL paths (`@odata.nextLink`) validated against
  a `*.businesscentral.dynamics.com` / `*.bc.dynamics.com` host
  allowlist before the bearer token is attached. Closes a
  bearer-exfiltration vector via tampered BC responses.
- **Medium:** CI hardened — third-party actions pinned by full commit
  SHA, default `permissions: contents: read`, `uv sync --locked` for
  reproducible installs from a committed `uv.lock`.
- **Medium:** Saved-query OData injection prevention (filter-context
  escape + per-parameter `type`/`pattern`/`min`/`max`/`enum`
  validation).

### Known limitations

- Alpha software. The SDK and CLI surface may change in 0.x releases;
  track this CHANGELOG for breaking changes.
- Some BC custom-API edge cases (zero-GUID ids on
  `SourceTableTemporary` pages) require the `--standard` flag to
  bypass the registry — see `bcli attach upload --help`.

## [Pre-0.1.0 history]

Earlier development happened under the working name `bcapi`, then
moved to `bcli` (April 2026), then to the PyPI distribution name
`bc-cli` (April 2026, after discovering the `bcli` PyPI name was
squatted by an unrelated 2018 package).

[Unreleased]: https://github.com/igor-ctrl/bcli/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/igor-ctrl/bcli/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/igor-ctrl/bcli/releases/tag/v0.1.0
