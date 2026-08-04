# Changelog

All notable changes to this project are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-08-04

### Added

- `StaticTokenAuth` and a new `auth=` parameter on `AsyncBCClient`, for embedders
  that already hold a Business Central access token. Pass a token string or a
  callable — a callable is re-invoked per request, so a long-running process
  picks up a refreshed token instead of pinning one that expires. Injected auth
  bypasses the profile's `auth_method` entirely, which is what lets a `browser`
  profile work somewhere that has no browser and no loopback listener to bind.
- `bcli.queries`, a reusable saved-query module extracted from the CLI: catalog
  loading, parameter validation against each parameter's declared
  type/pattern/min/max/enum, and `${{ params.X }}` resolution with OData
  escaping. `QueryCatalogError`, `QueryError` and `QueryParamError` are exported
  from the package root. `bcli q` is now a consumer of this module; its output,
  errors and exit codes are unchanged.
- Python 3.14 in the supported-versions classifiers. `requires-python` stays at
  `>=3.11`.

### Fixed

- The sdist no longer sweeps in nested checkouts. `[tool.hatch.build.targets.sdist]`
  listed patterns like `docs/` and `src/` without a leading slash, so they matched at
  any depth — and `.claude/worktrees/agent-*/` holds full copies of the repo. The
  0.7.0 sdist picked up 1350 extra files that way (2 MiB instead of 434 KiB),
  including older copies of `docs/configuration.md` and `docs/multi-company.md` from
  before example identifiers were replaced with placeholders. The wheel was never
  affected, since it builds from `packages`, and `git ls-files` was clean — only
  inspecting the built artifact showed it. Patterns are now anchored to the project
  root.

- `build_url` now validates `entity_set_name` and `record_id` as single URL path
  components, rejecting raw `/`, `\`, `?`, `#` and the `.`/`..` segments. Both
  values are spliced directly into the request path, so a record key containing
  a path separator could compose a URL addressing a different entity than the
  one the endpoint registry was consulted about — the registry lookup and the
  `disable_standard_api` gate both key on the entity-set name alone. Keys that
  legitimately contain quotes, commas, equals signs, hyphens or parentheses
  inside a quoted string are unaffected; percent-encode a separator that is
  genuinely part of a key. The same validation applies to the key inside a
  bound-action invocation, whose resolver checks the registry for the parent
  entity set only.
- `--dry-run` no longer reports success for a request the real command refuses.
  `try_resolve_url` never raises by design, so an invalid `record_id` was
  swallowed to `resolved_url: null` and the preview still rendered and exited 0
  while the real run exited 1 — the opposite of what a preview is for, and its
  documented consumers are agents parsing that envelope to decide whether to
  proceed. It now takes `strict=True` from the dry-run path, which re-raises
  invalid *input* while still swallowing *incidental* resolution failures (a
  registry miss keeps previewing with a null URL, as documented). `BCLIError`
  subclasses don't inherit `ValueError`, which is what makes that split clean.
  The failure is presented as the same `Error:` line the real run prints, not a
  traceback.
- An **empty** `record_id` is now an error instead of silently addressing the
  collection. `None` still means "operate on the entity set" (`bcli get
  <entity>` with no id is a collection read), but `""` previously took the same
  path — so a caller that meant one record and supplied nothing got a request
  against the whole set. `delete` and `patch` take `record_id` as a required
  positional, so `bcli delete <entity> ""` composed a DELETE against the entity
  set rather than a row.
- `test_no_context_policy_path` no longer asserts against the developer's real
  `~/.config/bcli`, so it passes on a machine that has recorded a bcli error
  rather than only on a clean CI home.

## [0.6.2] - 2026-07-22

### Fixed

- `bcli doctor` now honors the global `--profile`/`-p` flag. Previously
  `bcli --profile <name> doctor` was ignored and the report always reflected
  the default profile; `doctor` now falls back to the global profile when its
  own `--profile` isn't passed.
- The `--publisher`/`--group`/`--version` route-override options on
  `get`/`post`/`patch`/`delete` are no longer hidden from `--help`, so the hint
  printed by the "endpoint not found" error is actually discoverable.
- Registry-miss error messages no longer imply the route override can reach
  Microsoft's standard v2.0 entities — it targets custom
  `api/{publisher}/{group}/{version}` routes only.

### Security

- `build_url` / `build_metadata_url` now validate the custom-API route segments
  (`publisher`/`group`/`version`), rejecting empty/whitespace values and any
  segment containing `.`, `..`, `/`, or `\`. This closes a path-traversal
  (e.g. `--version ..`) that could otherwise normalize back to the standard
  `/api/v2.0/` route and defeat `disable_standard_api`.

## [0.6.1] - 2026-05-25

### Fixed

- **`bcli pack install` now writes registry presets in the array shape the
  runtime registry loader reads.** The installer previously stored
  `registries/<profile>.json` `endpoints` as a name→body object, but
  `EndpointRegistry` iterates `endpoints` as a JSON array — so pack-installed
  presets never resolved at query time (and merging into an existing
  array-shaped registry raised `InstallError`). Install and uninstall now
  read/merge/filter the array by each entry's `entity_set_name`. Added a
  regression test that loads an installed preset through the real registry
  loader and resolves it.

## [0.6.0] - 2026-05-25

### Changed — ETL stampers are now pluggable (BREAKING)

- **`bcli etl sync` no longer injects audit/metadata columns by default.**
  Output is a clean record shape; any extra columns are opt-in.
- **New `bcli.etl.stampers` entry-point group.** A plugin exposes a zero-arg
  callable returning a `Stamper` (`Callable[[list[dict]], list[dict]]` — a
  per-page row transform). The operator opts in by name via the new
  `[etl] stampers = ["..."]` config, or per-run with `bcli etl sync
  --stamper NAME` (repeatable). Unknown names are skipped with a warning;
  one broken plugin never aborts a sync. Mirrors the dispatch shape of
  the `bcli.telemetry` / `bcli.ask` factories.
- **New `EtlConfig` (`[etl]` config section)** with a `stampers: list[str]`
  field, wired into `BCConfig`.
- **`bcli_profile()` drops its built-in audit-column flag** in favour of
  the generic `stampers=[...]` argument (entry-point names) / `[etl]
  stampers` config. The generic `audit_stamper` / `company_id_stamper`
  helpers remain. Migration: if you relied on the previous default audit
  columns, install a package that registers the matching stamper plugin
  and add its name to `[etl] stampers`.

## [0.5.0] - 2026-05-25

### Added — Part 3 (`bcli-site/` landing page v0)

- **`bcli-site/`** — Astro + Tailwind landing page scaffold for
  bcli.sh. Single page (v0): hero, install instructions, three
  example commands, features grid, GitHub link.
- Stack: Astro 4 + Tailwind 3 + TypeScript 5 (`extends:
  astro/tsconfigs/strict`).
- Copy reflects what's actually shipped: packs, ask, MCP server,
  describe. Does NOT oversell the deferred `bcli agent` mode (R9).
- `.github/workflows/site.yml` builds the site on changes under
  `bcli-site/**`; Vercel deploy stub is wired but commented out
  until `VERCEL_TOKEN` etc. are added to repo secrets.
- `bcli-site/node_modules`, `dist`, `.astro`, and lockfiles are
  gitignored.

### Added — Part 2 (`bcli ask`)

- **`bcli ask "<question>"`** — second-opinion oracle. Bundles the
  operator's recent failing context (last-error, http-tail,
  profile, describe excerpt) via :mod:`bcli.context`, ships it to
  a configured LLM backend, and prints the answer. Opt-in: NullAsker
  is the default; set `[ask] backend = "claude"` (or `"openai"`) to
  activate.
- Built-in backends: `null` (default), `claude` (Anthropic — extras
  `[ask-claude]`), `openai` (extras `[ask-openai]`). Third-party
  backends register by import path `module.path:ClassName`. Mirror
  of the `extract` factory shape exactly.
- **`--dry-run`** prints the exact redacted bundle that would be
  sent before any network call. **`--no-context`** suppresses the
  auto-bundle. **`--attach PATH`** pins a file with redaction +
  truncation. **`--backend NAME`** is a one-shot override.
- **`bcli.ask.context_providers` entry-point group (R8)** —
  downstream packages add domain-specific context (glossaries,
  schema hints) via a registered callable. Strictly opt-in: a pack
  may recommend a provider but never auto-enables it; user config
  in `[ask] context_providers = [...]` is the binding decision.
- New `AskConfig` section in `bcli.config._model` exposing
  `backend`, `model`, `api_key_env`, `max_tokens`,
  `include_describe`, `include_http_tail`, `context_providers`.

### Added — Part 1 (`bcli pack`)

- **`bcli pack`** command group: `list`, `info`, `install`,
  `uninstall`. Discovers packs from three sources: built-in
  (``packs/`` in the repo), entry-point group ``bcli.packs``, and
  ``--path <dir>`` for local development.
- **Pack manifest format** (``pack.yaml``) with `agent_fragments`,
  `queries`, `batches`, `registry_presets`, and
  `recommended_context_providers`. Each fragment declares
  `targets:` (`agents` and/or `claude`); default `[agents]` (R3).
- **Install ledger** at
  ``~/.config/bcli/packs/<profile>/<pack>.json`` recording every
  artefact written, with per-entry `rendered_hash` and `owner` so
  uninstall is provenance-driven (R2).
- **Conflict detection** on registry presets (R7): a second pack
  cannot silently overwrite an endpoint owned by another pack —
  ``--replace-owned --accept-conflicts`` is the two-flag escape
  hatch.
- **Idempotent re-install**: marker blocks in AGENTS.md / CLAUDE.md
  are replaced in place via ID + content_hash, never duplicated.
- **Two built-in packs**: `starter-generic` (6 queries, 2 batches,
  3 fragments — uses only standard v2.0 endpoints) and
  `cronus-demo` (Microsoft CRONUS demo workflow).

### Added — Part 0 (context infrastructure for LLM features)

- **`bcli.context` package** — shared, model-bound context layer that
  future LLM-driven features consume (`bcli ask`, future `bcli agent`).
  Standalone in this release; no CLI consumers yet.
- **Typed `ContextBundle` dataclass** with `to_dict()` / `to_prompt_text()`
  renderers. Frozen, JSON-serialisable, token-budgeted with source
  attribution and an explicit `RedactionRecord` audit trail (R4).
- **Three-layer redaction** (`bcli.context._redact`) — composes the
  existing `bcli/audit/_redact.py` key-based stripper, the
  `bcli/telemetry/events.py` token-pattern regex, and a new URL
  query-param / GUID / attachment scrubber (R5). Every redaction is
  logged with a stable `rule_id` so regressions are catchable in CI.
- **Last-error capture** — central `BCLIError` handler now drops a
  redacted snapshot to `~/.config/bcli/last-error.json`. **No
  tracebacks by default**; `--debug` invocations also write a
  `last-error-debug.json` sidecar at mode 0600 (R6).
- **`bcli.http` rolling tail** — opt-in NDJSON tail at
  `~/.config/bcli/http-tail.ndjson` enabled by `[context] tail = true`.
  Size-bounded via `RotatingFileHandler`; URLs are query-stripped on
  read so the bundle stays safe.
- **`ContextConfig`** — new `[context]` config section with `tail`,
  `redact_company_ids`, `attachment_max_bytes` knobs.

## [0.4.0] — 2026-05-18 — Agent Interface Profile v0.1

The Agent Interface Profile (AIP) v0.1 lands: a small kernel of CLI
primitives that any agent runtime can drive deterministically, without
parallel schemas or hand-written MCP tools.

### Added

- **`bcli describe --format json`** — canonical machine-readable
  projection of the live Typer surface + endpoint registry + active
  profile. One command MCP, completions, and docs all consume; new CLI
  commands light up automatically. Cached at
  `~/.config/bcli/describe/<profile>.<hash>.json` with mtime
  invalidation. Subtree mode (`bcli describe get`, `bcli describe batch
  run`) returns narrow output for token-constrained agents. Includes
  forward-compat declarations: `emits_result_envelope`,
  `emits_operation_state`, `requires_confirmation: "production"`, plus
  the new `exit_codes` taxonomy and per-command `positionals` /
  `required` / `limits` extensions.
- **Mutation result envelope (`--result-out PATH` / `--result-fd N`)**
  on every mutating verb (`post`, `patch`, `delete`, `attach upload`,
  `batch run`). Frozen 18-field JSON envelope written atomically
  (`os.replace` + `fsync`); contains profile, environment, company,
  method, endpoint, resolved URL, record id, status, exit code, BC
  correlation id, started_at, duration_ms. Failed envelopes carry the
  exit code (4 = not found, 6 = remote 4xx, 7 = remote 5xx, 8 = policy
  refusal, etc.) so an agent can read it side-channel and act without
  scraping stdout. For `batch run` the envelope's `record_id` is the
  ledger run id — pivot directly to `bcli batch state <run-id>` for
  per-step detail.
- **Batch operation ledger (SQLite)** — one
  `~/.config/bcli/batch/<run-id>.db` per `bcli batch run` invocation.
  WAL + `synchronous=NORMAL`, intent row written before each HTTP call
  (survives SIGKILL); outcome row after. Derived run state
  distinguishes `partially_committed` from a stale `running` stamp. New
  commands: `bcli batch state <run-id>`, `bcli batch list [--state
  STATE] [--limit N]`, `bcli batch rollback <run-id> [--dry-run]
  [--yes]`. Rollback issues `DELETE` for committed POSTs only; PATCH /
  DELETE marked `rollback_skipped` (no clean inverse without pre-image
  snapshots). `disable_writes` is a hard refusal on rollback (no
  `--yes` bypass).
- **Exit code taxonomy** — `bcli.exit_codes` defines 0/1/2/3/4/5/6/7/8
  with short labels; `bcli describe` projects the map; centralized
  error handler maps `BCLIError` subclasses (`AuthError → 3`,
  `RegistryError → 4`, `ValidationError → 5`, `ConfigError → 2`,
  `SafetyError → 8`).
- **"Did you mean" remediation hints** on `BCLIError` paths: auth →
  `Run 'bcli auth login --profile X'`, config (no profiles) →
  `Run 'bcli config init'`, config (unknown profile) → fuzzy match,
  registry (no fuzzy) → `Run 'bcli registry import …'`.
- **JSON on pipe by default** — when stdout isn't a TTY and no
  `--format` was passed, emit JSON. Pipelines, redirects, CI steps,
  agent runtimes all get the canonical machine-readable shape with no
  flag dance. The `CLAUDECODE` and `BCLI_AGENT` env hints keep their
  markdown semantics (explicit user opt-in); legacy Windows console
  host stays on markdown for the mojibake reason. `BCLI_FORMAT` and
  explicit `--format` always win.
- **`--idempotency-key KEY`** on `post`, `patch`, `delete`, `attach
  upload`. IETF `Idempotency-Key` HTTP header sent on the first call
  (gateway-level dedup remains in play). Same-run replay protection in
  `bcli batch run`: if two mutating steps share an `idempotency_key:`
  in the YAML, the second is replayed (no second HTTP, no duplicate
  ledger row), and the result entry carries `prior_seq`,
  `prior_step_id`, `prior_bc_correlation_id`. Ledger schema migrates
  v1 → v2 non-destructively via `ALTER TABLE step ADD COLUMN
  idempotency_key`.
- **Progress events (`--progress-fd N`)** on `bcli batch run` and
  `bcli extract run`. JSON-lines `step_started` / `step_completed`
  written to a dedicated fd (separate from `--result-fd`). Stderr
  stays human-readable; the fd channel is structured and stable for
  agents to demux. Replayed steps emit a synthetic pair with
  `status="replayed"` so the progress stream tells the truth.
- **23 dynamically-generated MCP tools** in `bcli_mcp` (was 4
  hand-written). Server subprocesses `bcli describe` once on startup
  and registers one tool per command; new CLI commands light up as
  MCP tools automatically. Five new mutating tools (`bcli_post`,
  `bcli_patch`, `bcli_delete`, `bcli_attach_upload`, `bcli_batch_run`)
  pass `--result-out` and return the envelope as their tool result.
  `status="failed"` envelopes surface as MCP `ToolError` with the BC
  correlation id quoted.
- **`AsyncBCClient.delete_url(url, *, etag="*")`** — new SDK method
  for absolute-URL deletes (used by the rollback path; avoids
  re-resolving the registry at undo time).
- **`bcli skill install`** — generates `.claude/commands/bcli-<name>.md`
  per saved query and per batch template (`~/.config/bcli/batches/
  <profile>/*.yaml`). Generates a top-level
  `.claude/skills/bcli/SKILL.md` index grouped by `categories:`.
  SHA-256 content hash embedded in the provenance comment for
  byte-stable idempotency — no `generated_at` timestamp, so re-runs on
  unchanged sources are mtime-preserving no-ops. `manual: true` in a
  file's YAML frontmatter protects it from regeneration. `--dry-run`
  previews; `--target` resolves to explicit path > CWD with `.claude/`
  > `$HOME`. Stdlib only — no jinja2; atomic writes via
  `tempfile.mkstemp` + `os.replace`.
- **`bcli skill init`** — interactive wizard that reads `bcli describe
  --format json` via subprocess, runs 4 Rich prompts (role / top-three
  daily questions / slash-command style / generate-new-queries y/N),
  fuzzy-matches existing saved queries against the top-three free
  text (stdlib `difflib`), and proposes new role-tailored queries via
  entry-point providers — each with a per-query `[y/N]` approval gate.
  Generates `~/.claude/skills/bcli-<user>/SKILL.md` with YAML
  provenance frontmatter. Atomic commit phase: snapshots existing
  content, writes all targets, restores on first failure. Guardrails
  via `_assert_writable` restrict writes to `~/.config/bcli/queries/`,
  `~/.claude/skills/bcli-<user>/`, and `~/.config/bcli/skills/`;
  symlink-safe via `Path.resolve(strict=False)` + `is_relative_to`.
- **`bcli skill update`** — idempotent re-run via state cache at
  `~/.config/bcli/skills/.last-init.json`. The cache persists both
  the interview answers AND the approved-query bodies so a later
  `--non-interactive` replay re-writes the same queries verbatim
  (without re-asking the operator). Describe-payload-hash mismatch
  refuses silent replay and asks for a re-interview when the describe
  surface changed under the user's feet.
- **Saved-query YAML schema extension** — three additive fields
  (`description`, `categories`, `args`). Existing saved-query bundles
  without these still work: when `args:` is omitted, `bcli skill
  install` derives it from `params:` keys (required first, optional
  second, both in YAML insertion order). Documented in
  `docs/saved-queries.md`'s new "Slash-command projection" section.
- **Entry-point group `bcli.skill_init.role_templates`** — OSS bcli
  ships with an opinion-free default proposer (returns `[]` for every
  role). Downstream packages plug in role templates by registering
  callables under this entry-point group via standard Python
  packaging. Discovered at wizard time via
  `importlib.metadata.entry_points`. The provider signature is
  `(interview, payload) -> list[ProposedQuery]`; downstream
  integrators publish their own integration documentation.

### Changed

- **Policy refusal exit code 1 → 8.** Scripts that grep `if exit==1`
  for "the read-only profile blocked me" need updating. Agents
  consuming `bcli describe`'s new `exit_codes` field pick up the new
  code automatically.
- **MCP tool renames** (breaking for existing MCP clients — Claude
  Desktop, MCP Inspector configs referencing the old names need an
  update):
  - `query` → `bcli_get`
  - `list_endpoints` → `bcli_endpoint_list`
  - `describe_endpoint` → `bcli_endpoint_info` (with
    `bcli_endpoint_fields` split out for field discovery)
  - `list_companies` → `bcli_company_list`

  Migration table in `docs/mcp-server.md`. Tool names now consistently
  match the CLI command path.
- **MCP `bcli_get --top` cap remains 50 default / 1000 max** — parity
  with the pre-rewrite hand-written `query` tool. CLI shell users can
  still pass `--top 100000` directly; the cap is enforced only at the
  MCP schema level (advisory for agent runtimes).
- **Stderr routing for `bcli batch run` metadata** — the ledger path
  and run id print to stderr, not stdout. Stdout matches legacy batch
  output byte-for-byte (required by the additive constraint).

### Fixed

- **Clean SIGPIPE handling for piped output** — `bcli <cmd> | head`,
  `| grep -m 1`, and similar pipe-truncating consumers now terminate
  the CLI silently, matching `cat` and `grep` conventions, instead of
  emitting a `BrokenPipeError: [Errno 32] Broken pipe` traceback at
  interpreter shutdown. Implemented as a new `bcli_cli.app:main`
  console-script entry point that installs `SIGPIPE -> SIG_DFL` on
  POSIX with a `BrokenPipeError` safety net for Windows.
- **Hyphenated saved-query param names** — the workflow template
  resolver now accepts hyphens in identifiers, so references like
  `${{ params.vendor-no }}` substitute correctly. Previously the regex
  matched only `[\w.]`, silently leaving the literal `${{ … }}` token
  in the rendered filter (BC then 400'd or, worse, returned mismatched
  rows). Affects both `bcli q` saved queries and `bcli batch`
  workflows.

### Breaking changes

The two items above (exit code 1→8 + MCP tool renames) are intentional
breaking changes called out in the AIP plan. Both have one-line
migration paths. Skill install / skill init / skill update are
additive — no breaking changes from the Skills layer.

### Deferred to v0.5

- **Cross-run idempotency replay** — would require scanning every
  `*.db` in `~/.config/bcli/batch/` on each mutating call. Same-run
  protection covers the agent-retry case which is the common one;
  gateway-level Idempotency-Key dedup covers the rest.
- **`batch run --idempotency-key`** as a run-level flag — collides
  across multiple mutating steps. Per-step `idempotency_key:` in the
  batch YAML is the correct surface.
- **`telemetry_event_id` / `audit_log_offset` on the envelope** —
  currently always `null`. Wiring requires extending the
  `TelemetrySink` and audit protocols to return the emitted event id /
  log offset, touching every backend including the optional Azure
  Monitor extra.
- **Plan-token binding for single mutations** — `batch run --plan-out`
  works; `bcli post --plan-out` does not. Defer until requested.
- **Direct FastMCP schema-introspection test** — the `__signature__`
  patch is indirectly covered via tool-list registration tests; a
  future FastMCP upgrade could silently degrade tool input schemas
  without breaking tests.
- **Etag capture in ledger** — rollback DELETEs use `etag="*"`. A
  future concurrent edit between POST and rollback could clobber.
- **CWD-relative batch template discovery** for `bcli skill install` —
  today only `~/.config/bcli/batches/<profile>/*.yaml` is scanned;
  project-local batches in `./batches/` would also be useful for the
  per-project `.claude/` workflow.
- **SKILL.md frontmatter `generated_at` churn cleanup** — the
  timestamp ticks forward on every `bcli skill update
  --non-interactive` replay, so the frontmatter changes even when the
  body is byte-stable. Idempotency tests compare body-only; downstream
  content-hash watchers would see noise. Cosmetic.
- **`bcli skill update` separated from `init`** — today
  `update_command` delegates to `init_command(...)` verbatim.
  Documented for future evolution.
- **Public `bcli.skill_init` namespace for the entry-point contract**
  — downstream packages currently couple to
  `bcli_cli.commands.skill_init_cmd` for `InterviewState` /
  `ProposedQuery`. A future release could promote the protocol types
  to a public `bcli.skill_init` namespace.

## [0.2.0] — 2026-05-06

### Added

- **Structured `--dry-run` output** — write commands (`post`, `patch`,
  `delete`, `attach upload`) now emit a stable JSON envelope on stdout
  when `--format json` / `ndjson` / `raw` is selected. Includes
  `dry_run`, `method`, `endpoint`, `resolved_url`, `profile`,
  `environment`, `company_id`, `body`, and `record_id` (when applicable).
  Agents can parse the envelope before deciding whether to proceed. The
  human format keeps the same yellow rich panel on stderr but is now
  augmented with the resolved URL and profile context. See
  `docs/write-operations.md`.
- **Opt-in audit log** — new `[audit]` config section persists every
  write to a per-profile JSONL file. Each entry captures the resolved
  URL, response status, BC `correlation_id`, latency, redacted request
  body, and outcome (`completed` / `failed` / `dry_run`). Bounded disk
  usage via single-backup rotation. SDK (`AsyncBCClient`) does NOT
  auto-emit; this is a CLI-layer ergonomic on top of BC permission sets.
  See `docs/configuration.md#audit-log`.
- **Endpoint `caution` flag** — `EndpointMetadata` now carries a
  `caution: low | medium | high` level. Importers populate it
  automatically from a verb-name heuristic (entities containing `post`,
  `release`, `cancel`, `void`, `reverse`, `apply`, `unapply` are flagged
  `high`). Surfaced in `bcli endpoint info` and the `list_endpoints` MCP
  tool so agents can require explicit user confirmation before mutating
  posted/closed records.
- New `AGENTS.md` recipes for dry-run-first writes, caution-level
  interpretation, and audit-log location.

## [0.1.5] — 2026-05-05

### Added

- **Business Central admin setup guide** — new
  `docs/business-central-admin-setup.md` walks a zero-knowledge user
  through Entra app registration, localhost redirect setup, delegated BC
  permissions, admin consent, BC user permission sets, first `bcli
  config init`, and verification.
- **`bcli-mcp` preview server** — an MCP (Model Context Protocol) server
  that lets Claude Desktop and other MCP clients drive bcli. Four
  read-only tools: `query`, `list_endpoints`, `describe_endpoint`,
  `list_companies`. Subprocess-only architecture inherits profile, auth,
  retry, telemetry, and `disable_writes` from the CLI. Install with
  `pip install "bc-cli[mcp]"`. See `docs/mcp-server.md`.

### Changed

- `bcli config init` now defaults to browser PKCE auth for local humans
  and agents. New `--automation` and `--headless` shortcuts create
  client-credentials and device-code profiles respectively.
- CLI runtime dependencies now ship with the base `bc-cli` install, so
  `pip install bc-cli` and `uv tool install bc-cli` provide a working
  `bcli` command without requiring an extra.
- `bcli company list` accepts `--format` (`json`, `markdown`, `csv`,
  `ndjson`, `table`). Stable JSON shape:
  `[{"id", "name", "alias", "is_default"}]`.
- `bcli endpoint list` and `bcli endpoint info` accept `--format json`.
  Stable JSON shapes documented inline in each command's help text.

### Removed

- Removed WorkOS AuthKit support. Browser PKCE is now the delegated auth
  path, Business Central remains the permission boundary, and
  client-credentials profiles cover automation.

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

[Unreleased]: https://github.com/igor-ctrl/bcli/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/igor-ctrl/bcli/compare/v0.2.0...v0.4.0
[0.2.0]: https://github.com/igor-ctrl/bcli/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/igor-ctrl/bcli/compare/v0.1.2...v0.1.5
[0.1.2]: https://github.com/igor-ctrl/bcli/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/igor-ctrl/bcli/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/igor-ctrl/bcli/releases/tag/v0.1.0
