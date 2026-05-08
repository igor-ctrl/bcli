# Beautech Team Deployment Plan

Status: draft. **Beautech-internal bootstrap document, not part of the OSS bcli roadmap.** The bcli OSS tool ships independently of this plan; the work below describes how Beautech rolls bundles + diagnostics to its own finance and engine-tech teams on top of the upstream substrate.

Target: finance team (~10 users) + engine technical team (~10 users) on the existing scoped-profile substrate.

## Why this plan exists

bcli is moving from solo developer to a real team rollout. The current substrate already supports it (sandboxed profiles, curated registries, scoped saved queries, device-code auth, read-only-by-permission-set), but three workflow gaps will dominate first-month support load:

1. **No team-wide registry/query distribution.** Whoever last updated the JSON wins. New hires re-discover everything.
2. **No diagnostic surface.** Users with broken setups can't self-rescue. "Wrong profile / stale bundle / not authenticated / wrong company" will be every other support ticket.
3. **No discoverability for the saved-query library.** Finance ops will not learn YAML. They will ask "is there a query for overdue intercompany invoices?" via email.

This plan ships three boring high-leverage things to address (1)–(3), explicitly defers Redis and response caching until telemetry justifies them, and locks in trigger conditions so the deferral doesn't become indefinite.

## Phase 0 — pre-flight (this sprint)

- Decide bundle storage backend: pick whichever of `S3`, `Azure Blob`, or `GitHub Releases` the org already authenticates to cleanly. Decision lives in `docs/plans/team-deployment.md` once made.
- Identify two bundle owners: one finance, one engine-tech. They are the publish path.
- Land a minimal telemetry sink config so phase 4 has data when it's time. The pluggable `[telemetry]` substrate already exists at `src/bcli/telemetry/`; pick `console` for dev, set up Azure Monitor or a custom HTTP sink for prod. Capture `bcli.command`, `bcli.query`, `bcli.error` at minimum. **Do not** capture filter text or UPN unless privacy review approves.

## Phase 1 — `bcli doctor` (ships first)

Self-rescue command for non-technical users. This alone will eliminate most week-one tickets.

### Surface

```
bcli doctor [--profile <name>] [--json]
```

Default output: human-readable, color-coded (green/yellow/red per check), with a one-line verdict at the bottom (`OK`, `WARN`, or `FAIL`). `--json` for scripting. Non-zero exit on `FAIL`.

### Checks

| Check | Source | Fail condition |
|---|---|---|
| Active profile | `CLIState` resolved profile | Missing or unknown profile name |
| Bundle version | `manifest.json` `version` field (phase 2) | Missing manifest, or `last_refresh > 30d` |
| Signature verified | bundle signature check (phase 2) | Signature missing or invalid |
| Last refresh time | bundle metadata | > 7d warn, > 30d fail |
| Registry endpoint count | `EndpointRegistry.list_all()` | 0 endpoints in scoped profile |
| Saved query count | `queries/<profile>.yaml` | File missing in scoped profile |
| Field-list coverage | count of endpoints with `field_names` populated | Warn under 50% for scoped profiles |
| Auth mode | profile config | Unknown mode |
| Auth status | non-blocking probe of cached token | Expired and no refresh path |
| Company | `--company` resolution | No default and no override available |
| Environment | profile config | Missing |
| Tenant ID | profile config | Missing |
| BC connectivity | one-shot `GET companies` with 5s timeout | Non-2xx |
| Local overlay present | overlay file exists (phase 2) | Informational only |

### Output sketch

```
bcli doctor — profile: finance

  ✓ Active profile        finance
  ✓ Bundle version        2026.05.07-1 (signed by ops-bcli-bot)
  ✓ Last refresh          2 days ago
  ✓ Registry              42 endpoints, 38 with field lists (90%)
  ✓ Saved queries         17 queries
  ✓ Auth                  device_code, token valid for 47 min
  ✓ Tenant                contoso.onmicrosoft.com
  ✓ Environment           production
  ✓ Company               BTALI (default)
  ✓ BC connectivity       reachable, 312 ms
  ⚠ Field coverage        2 endpoints below 80% (run `bcli endpoint fields ...`)

  Verdict: OK
```

### Files to add/modify

- New: `src/bcli_cli/commands/doctor_cmd.py`
- New: `src/bcli/diagnostics/_checks.py` (testable check primitives, returns `CheckResult` with status + message)
- Modify: `src/bcli_cli/app.py` to register the command group
- New: `tests/test_diagnostics/test_checks.py`

### Done when

- `bcli doctor` runs in under 3 seconds for a healthy install.
- Each check is independently unit-tested with parametrized fail cases.
- Engine-tech and finance both ran it on their own laptop and the output made sense without explanation.

## Phase 2 — signed bundle distribution

### Bundle layout

```
finance-2026.05.07-1.tar.gz
  manifest.json
  registry.json          # mirrors current ~/.config/bcli/registries/<profile>.json
  queries.yaml           # mirrors current ~/.config/bcli/queries/<profile>.yaml
  field_lists.json       # pre-warmed field discovery (avoids first-touch tax)
  README.md              # human notes for this bundle version
```

`manifest.json`:

```json
{
  "schema_version": 1,
  "profile": "finance",
  "version": "2026.05.07-1",
  "published_at": "2026-05-07T14:32:00Z",
  "publisher": "ops-bcli-bot",
  "checksum_sha256": "…",
  "signature": "…",
  "min_bcli_version": "0.3.0",
  "previous_version": "2026.04.30-2",
  "release_notes": "Added overdue-ic and posted-invoice-by-id queries"
}
```

### Storage + transport

- Backend: signed HTTPS, single source of truth per profile. Bundles published to versioned object keys, e.g. `https://bundles.example.com/bcli/finance/2026.05.07-1.tar.gz` with a `latest.json` pointer for resolution. The org-specific URL lives in `~/.config/bcli/config.toml` as `[bundle.finance] url = "..."` so different teams can self-host.
- Signing: detached signature (`minisign` or `cosign`, decision pending) with the public key shipped in the user's profile config. Refresh fails closed if signature does not verify.
- **Not** `git pull`. Maintainers can author bundles in a private GitHub repo and ship via Release assets, but the user-facing transport is a signed tarball over HTTPS.

### `bcli config refresh` UX

```
bcli config refresh                       # refresh active profile
bcli config refresh --profile engine-tech # explicit profile
bcli config refresh --dry-run             # show diff vs local, no writes
bcli config refresh --rollback            # restore previous version
bcli config refresh --check               # exit code only, no output (cron-friendly)
```

Non-interactive, atomic. Output:

```
Refreshing finance from https://bundles.example.com/bcli/finance/latest.json
  Current:  2026.04.30-2
  Latest:   2026.05.07-1 (published 2 days ago by ops-bcli-bot)
  Verifying signature… ok
  Diff:
    + 2 endpoints (postedSalesInvoices, salesInvoiceLines)
    + 3 saved queries (overdue-ic, posted-by-id, customer-aging)
    ~ 1 query updated (open-pos: added customer parameter)
  Applied. Previous version retained at ~/.config/bcli/registries/finance.2026.04.30-2.json
```

### Overlay semantics

- Team bundle is **authoritative** for scoped profiles. `bcli config refresh` overwrites `registry.json` and `queries.yaml` atomically (write-temp + rename). Previous version retained for one rollback.
- Local overlay file `~/.config/bcli/overlays/<profile>.yaml` exists *only if* the profile config has `allow_local_overrides = true`. Off by default for sandboxed domain profiles.
- Effective view: team bundle merged with overlay, **team wins on name conflicts**. No interactive merge prompts ever. Non-technical users never see a conflict.
- `bcli endpoint fields` discovery for sandboxed profiles writes to overlay if enabled, otherwise informs the user to send the discovered fields to their bundle owner.

### Files to add/modify

- New: `src/bcli/bundle/__init__.py` (manifest schema, signature verification, atomic apply)
- New: `src/bcli/bundle/_fetch.py`, `_verify.py`, `_apply.py`, `_rollback.py`
- New: `src/bcli_cli/commands/refresh_cmd.py` (registered as `bcli config refresh`)
- Modify: `src/bcli/config/_loader.py` to compose registry from team bundle + optional overlay
- Modify: `src/bcli/registry/_registry.py` to load from the new layered path
- New: `examples/bundles/sample-bundle.tar.gz` + a `make-bundle` script for admins
- New: `docs/team-bundles.md` covering the publish workflow

### Done when

- An admin can produce a signed bundle with one command and publish it.
- A finance user can run `bcli config refresh` cold and have a working setup in under 30 seconds.
- `--rollback` restores the previous version verifiably.
- Tampered bundle is rejected with a clear error, not silently applied.

## Phase 3 — query metadata extension

Saved queries get richer descriptive metadata so substring + tag search beats the discoverability problem without embeddings.

### YAML schema additions

```yaml
queries:
  overdue-ic:
    description: Overdue intercompany invoices for a vendor
    aliases: [overdue-intercompany, ic-overdue, ar-overdue-ic]
    tags: [period-close, ap, intercompany]
    owner: finance-ops
    freshness: live  # one of: live, daily, reference
    examples:
      - bcli q overdue-ic vendor=ACME-IC
      - bcli q overdue-ic vendor=ACME-IC days=30
    related: [open-invoices, vendor-aging]
    params:
      vendor: { required: true, hint: "BC Vendor No." }
      days:   { default: 30, hint: "Days overdue" }
    # Query body lives at the top level (matches the runtime — there is
    # no `odata:` wrapper). The metadata block above is purely for
    # discoverability; nothing in it changes how the query executes.
    endpoint: vendorLedgerEntries
    filter: "vendorNumber eq '${{ params.vendor }}' and dueDate lt now sub '${{ params.days }}d' and remainingAmount gt 0"
    orderby: dueDate
```

### Search surface

```
bcli q list                                 # all queries, table
bcli q list --tag period-close              # filter by tag
bcli q list --owner finance-ops             # filter by owner
bcli q search "overdue invoices"            # substring + alias + description match
bcli q info overdue-ic                      # full metadata view
```

`bcli q search` ranks by: exact name > alias hit > tag hit > description substring > example substring. No embeddings. Honest "no match, did you mean X" output when the score floor isn't met.

### Files to add/modify

- Modify: `src/bcli/workflow/` query schema (extend Pydantic model)
- Modify: `src/bcli_cli/commands/query_cmd.py` to add `list`, `search`, `info` subcommands
- New: `tests/test_workflow/test_query_metadata.py`
- Update: `docs/saved-queries.md` with the schema additions
- Migration: existing queries without the new fields keep working (all new fields optional)

### Done when

- Existing queries still run unchanged.
- `bcli q search` finds an existing query when the user types a plausible NL phrase.
- Finance ops can browse the catalog by tag without reading YAML.

## Phase 4 — response caching (deferred, telemetry-gated)

**Do not ship until all three triggers are met:**

1. Telemetry shows P95 latency for posted-invoice / open-PO endpoints exceeding 2s under finance close-week load.
2. Telemetry shows ≥ 5% of GETs returning 429 / 503 during close week.
3. The two bundle owners agree the workflow pain is real, not theoretical.

If shipped:

- Backend: `hishel`-backed disk cache around `httpx`, **not** Redis. Single-process. Lives at `~/.config/bcli/cache/`.
- Cache key composition: `tenant_id + environment + company + profile + resolved_url + sorted(query_params) + select_hash`. Never less.
- TTL ceilings (max — actual values configurable per endpoint, can be lower):
  - Vendor balances: no cache by default; 5-15s if forced, output labeled `(cached 8s ago)`
  - Open POs / open invoices: 15-60s
  - Inventory / utilization / preservation status: 10-60s
  - Posted invoice **list** queries: 60-300s only when filtered to a closed period
  - Posted invoice / journal entry **by exact record ID**: 1-24h (immutable post-posting)
- Never cache `--all`. Never cache write-adjacent commands. Cache hits are visible in output and structured logs.
- Opt-in per profile via `[cache] enabled = true`.

## Phase 5 — Redis-for-AI (deferred, condition-gated)

**Do not ship until at least one of:**

1. A centralized `bcli-mcp` service is running for multiple agents/users (cross-process state stops being free).
2. Saved-query library exceeds 200 entries with measurable search misses in telemetry.
3. Field-list "did you mean" produces wrong suggestions ≥ 5% of attempts measurably.

If shipped, the integration points are:

- Vector "did you mean" over discovered field names (replaces substring fuzzy in `src/bcli/client/_async.py:469`).
- Vector search over saved queries by NL intent. Caches **query plans**, never query results.
- Shared field-discovery cache for the centralized MCP path.

Backend: pluggable `cache_backend` with `redis` extra, mirroring the existing `[telemetry]` pattern. NullCache default. Redis is optional infrastructure.

## Out of scope

- Semantic caching of OData result data. Stale balances / inventory / posted-document state silently returned would destroy trust in bcli as a BC truth source. If we want a low-risk reference subset later, it lands as an explicit feature with `cached as of` labels in output, not a silent layer.
- Token sharing across users. Each user authenticates as themselves. The BC permission set is the security boundary, not the bcli flag.
- Replacing the existing per-profile registry JSON layout. Bundles are a publish-and-distribute layer on top, not a replacement.

## Risks and open questions

- **Beautech rollout gate: publisher signing.** The current
  `Sha256Verifier` only proves internal consistency: each file matches
  its declared hash, and the manifest's roll-up matches the contents
  map. It does NOT authenticate the publisher. A compromised CDN can
  mint a malicious `registry.json`, recompute the hashes, and pass
  verification. Before Beautech rolls bundles to finance / engine-tech,
  either ship a real cryptographic signer (`minisign` / `cosign` /
  ed25519 + pinned key) at the `bcli.bundle.Verifier` seam, or restrict
  bundle distribution to private blob storage with org-level auth and
  treat HTTPS+auth as the trust boundary. Document the choice
  internally; do not enable `bcli config refresh` for finance/engine-
  tech without one of the two. Note: this is a Beautech deployment
  gate, not an OSS bcli release gate — the upstream tool can ship the
  bundle infra without dictating how operators use it.
- **Signing key custody.** Who owns the bundle signing key, and how is it rotated when an owner leaves? Decide before phase 2 ships.
- **Bundle URL discovery.** First-time install needs to know where to refresh from. Likely `bcli config init --scoped --bundle-url <url>` extends the existing wizard. Verify this fits the wizard's current shape.
- **Field discovery in scoped profiles.** Today `bcli endpoint fields` writes back to the local registry. With overlay-off-by-default, sandboxed users can't improve their own setup. The plan: those discoveries get logged to a "candidate fields" file the user can email to their bundle owner. Better mechanism welcome.
- **Bundle drift between teams.** If finance and engine-tech import the same standard endpoint into both bundles and they diverge, which wins? Today: each profile is isolated. Keep it that way.
- **Telemetry privacy.** Phase 0 telemetry must not capture filter text or UPN by default. Confirm with the legal/privacy reviewer.

## Validation gates per phase

| Phase | Gate |
|---|---|
| 1 | `bcli doctor` runs on engine-tech and finance laptops cold; output makes sense to a non-developer |
| 2 | Bundle round-trip works: admin publishes, user runs `refresh`, signature verifies, rollback works |
| 3 | Existing queries unchanged; `q search "overdue invoices"` finds `overdue-ic` |
| 4 | Telemetry triggers met before any code is written |
| 5 | At least one of three triggers met before any code is written |

## Sequencing summary

1. **Now:** phase 0 (pick backend + telemetry sink) and phase 1 (`bcli doctor`). Two weeks.
2. **Next:** phase 2 (bundle distribution + `config refresh`). Three weeks.
3. **After phase 2 ships and bakes for two weeks:** phase 3 (query metadata + search). One week.
4. **On telemetry:** phase 4. Maybe never.
5. **On condition trigger:** phase 5. Maybe never.

The honest read: phases 1–3 are most of the user-facing value. Phases 4–5 are the interesting features but only earn their cost under conditions we haven't observed yet.
