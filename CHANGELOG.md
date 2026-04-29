# Changelog

All notable changes to this project are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] — 2026-04-29

Security release. Closes four findings from a strix.ai run against the
repo. No public SDK signature changes; the CLI gains one new flag
(`bcli batch run --yes`).

### Security

- **vuln-0001 (HIGH, CWE-352)** — WorkOS localhost callback now binds a
  per-login high-entropy `state` token. Before this release, any local
  request reaching `127.0.0.1:8401/callback?code=…` during the login
  window would be exchanged for a role-bearing WorkOS identity and
  cached on disk. The handler now rejects callbacks whose path is not
  `/callback` (404) or whose `state` doesn't match the per-login token
  (400), and surfaces invalid callbacks as auth failures rather than
  masking them as timeouts.
- **vuln-0002 (MEDIUM, CWE-841)** — `bcli batch run` now enforces the
  `disable_writes` profile gate that direct `post`/`patch`/`delete`
  commands already honour. Mutating batch steps on a read-only profile
  prompt for confirmation interactively or abort with exit 1 in
  non-interactive sessions. New `--yes` / `-y` flag opts scripted use
  past the prompt. Pure GET batches are unaffected, and `--dry-run`
  still skips the gate so workflows can be previewed.
- **vuln-0003 (MEDIUM)** — Browser auth callback listener now binds an
  ephemeral kernel-assigned port instead of a hard-coded 8400, and
  serves continuously until a state-bound callback arrives or the
  timeout expires. Stray requests (e.g. `/favicon.ico`) and
  state-mismatched callbacks no longer consume the only callback slot.
  Microsoft Entra accepts any port for `http://localhost` redirect URIs
  on public clients per RFC 8252, so existing app registrations
  continue to work without changes.
- **vuln-0004 (HIGH, CWE-841)** — `SafeContext` writes are now bound to
  the explicit `environment` and `company_id` passed to
  `client.safe_write(env, company)`, not the client's profile-bound
  target. Previously the safety gate validated operator intent but the
  underlying URL still resolved against the profile, so writes inside
  `safe_write("Sandbox", "company-SANDBOX")` could still hit
  `Production/company-PROD`. Closes the documentation-vs-behaviour
  mismatch where the README/changelog claimed the gate prevented
  wrong-environment writes.

### Changed

- `bcli batch run` accepts a new `--yes` / `-y` flag (see vuln-0002
  above). Existing automation against writable profiles is unaffected;
  CI scripts that run mutating batches against a `disable_writes`
  profile must pass `--yes` or migrate to a writable profile.

## [0.1.1] — 2026-04-29

OSS-readiness polish. No SDK behaviour changes; one CLI rename and a
README/docstring cleanup pass.

### Changed

- **Renamed the document-attachment CLI command** to `bcli attach`,
  with subcommands `bcli attach upload` and `bcli attach test`.
  Functionality is unchanged. The previous name was a customer-name
  leftover from internal development; the workflow is generic to any
  Business Central tenant.
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
