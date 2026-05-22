# Implementation summary — Parts 0-3 (pack / ask / site plan)

Worktree: `/Users/igor/Projects/2_Areas/D_Internal_CLI_Tooling/bcli/.claude/worktrees/agent-a5724d8b39c9ef6d9`
Branch: `worktree-agent-a5724d8b39c9ef6d9`
Plan: `/Users/igor/.claude/plans/how-would-the-bcli-zippy-lantern.md`

Total commits on this branch: **11** (one per logical sub-unit).

## Part 0 — `bcli.context` infrastructure (R1, R4, R5, R6)

Status: **shipped, green** — 40 tests pass, ruff clean.

Files:
- `src/bcli/context/__init__.py` — public surface
- `src/bcli/context/_protocol.py` — pre-existing skeleton, refined
  with `to_prompt_text()` Markdown renderer (no other changes —
  protocol was already R4-aligned)
- `src/bcli/context/_redact.py` — three-layer redaction composing
  `bcli/audit/_redact.py` (keys) + `bcli/telemetry/events.py` regex
  (patterns) + new URL/GUID/attachment scrub. Five stable
  `rule_id` constants.
- `src/bcli/context/_last_error.py` — captures `BCLIError` exits
  to `~/.config/bcli/last-error.json`. No tracebacks by default;
  `--debug` runs also produce `last-error-debug.json` at mode 0600.
- `src/bcli/context/_http_tail.py` — `RotatingFileHandler` on the
  `bcli.http` logger; opt-in via `[context] tail = true`.
- `src/bcli/context/_bundle.py` — `build_bundle()` pure function;
  token-budgeted priority truncation (question > last_error >
  profile > http > describe > attachments).
- `src/bcli/config/_model.py` — `ContextConfig` added.
- `src/bcli_cli/app.py` — central error handler now calls
  `capture_last_error`; root callback bootstraps the http-tail
  handler when configured.

Tests: 40 in `tests/test_context/` covering dataclass round-trip,
3-layer redaction (adversarial nested JSON, URL-encoded tokens,
base64 JWTs), audit-trail completeness, last-error capture w/o
tracebacks, http-tail rotation + size cap, bundle composition
with all sources, no-context path.

Commits:
- `f3f448f feat(context): bcli.context — typed ContextBundle + 3-layer redaction`
- `fa0ebcb feat(context): wire last-error capture + http-tail bootstrap into CLI`
- `2eb26a3 test(context): cover dataclass round-trip, 3-layer redaction, audit trail`
- `e3b1714 docs(changelog): note Part 0 (bcli.context infrastructure)`

## Part 1 — `bcli pack` (R2, R3, R7, R8)

Status: **shipped, green** — 19 tests pass, ruff clean. Both
built-in packs install end-to-end against a tmp config dir.

Files:
- `src/bcli/packs/_protocol.py` — frozen dataclasses (Pack,
  PackManifest, PackContents, AgentFragment, PackQuery, PackBatch,
  PackRegistryPreset). `targets: [agents] | [claude] | [agents, claude]`
  per fragment (R3 default `[agents]`).
- `src/bcli/packs/_loader.py` — manifest + content loader with
  schema validation.
- `src/bcli/packs/_registry.py` — discovery: built-in (`packs/`)
  + entry-point group `bcli.packs` + local path. Wheel-install
  fallback via `bcli/packs/_builtin/`.
- `src/bcli/packs/_ledger.py` — JSON ledger at
  `~/.config/bcli/packs/<profile>/<pack>.json` (R2). Frozen
  dataclasses; atomic write.
- `src/bcli/packs/_installer.py` — `plan_install` + `execute_install`
  + `uninstall_pack`. Marker blocks with content_hash; idempotent
  re-install; provenance-injected registry presets; conflict
  detection refuses unless `--replace-owned --accept-conflicts` (R7).
- `src/bcli_cli/commands/pack_cmd.py` — `bcli pack list / info /
  install / uninstall`. Dry-run; per-install confirm; pack
  recommendations surfaced as hints, never auto-enabled (R8).
- `packs/starter-generic/` — 6 queries, 2 batches, 3 fragments;
  standard v2.0 endpoints only.
- `packs/cronus-demo/` — Microsoft CRONUS month-end demo (lifted
  from `examples/`).
- `pyproject.toml` — `[tool.hatch.build.targets.wheel.force-include]`
  maps `packs/` → `bcli/packs/_builtin` so the wheel ships the
  built-ins.

Tests: 19 in `tests/test_packs/` covering manifest validation,
fragment-targets routing (agents vs claude vs both), install
round-trips, idempotency, conflict detection, uninstall,
discovery, broken-pack tolerance, and end-to-end install of both
built-in packs.

Smoke-test (manually run):
- `bcli pack list` shows both built-in packs.
- `bcli pack info starter-generic` shows manifest + content
  counts + "not installed on profile X".
- `bcli pack install starter-generic --dry-run --target /tmp/X`
  prints the full plan (3 fragments + 3 marker blocks + 6 queries
  + 2 batches) without touching disk.

Commits:
- `e3eb1eb feat(packs): bcli.packs SDK — Pack/Manifest/Ledger + installer (R2, R3, R7, R8)`
- `c61627b feat(packs): bcli pack list / info / install / uninstall CLI`
- `a6a8c46 feat(packs): ship starter-generic + cronus-demo built-in packs`
- `ebc0544 test(packs): 19 tests + pyproject pack wheel layout + changelog`

## Part 2 — `bcli ask`

Status: **shipped, green** — 16 tests pass, ruff clean. CLI
smoke-test (`bcli ask --dry-run --no-context "test"`) prints the
redacted bundle without making a network call.

Files:
- `src/bcli/ask/_protocol.py` — `AskBackend` Protocol + NullAsker
  (mirror of `bcli/extract/_protocol.py`).
- `src/bcli/ask/_factory.py` — `get_asker` dispatch with
  `_BUILTIN_BACKENDS` + `module:Class` fallback + Null fallback
  with one-shot warning (mirror of `bcli/extract/_factory.py`).
- `src/bcli/ask/_claude.py` — Anthropic backend
  (`messages.create`, bundle as Markdown user-turn).
- `src/bcli/ask/_openai.py` — OpenAI Responses API backend.
- `src/bcli/ask/_providers.py` — `bcli.ask.context_providers`
  entry-point group (R8). Opt-in via `[ask] context_providers`.
- `src/bcli_cli/commands/ask_cmd.py` — `bcli ask "<q>"` with
  `--no-context`, `--attach`, `--backend`, `--dry-run`,
  `--include-bodies`, `--include-debug`, `--max-tokens`.
- `src/bcli/config/_model.py` — `AskConfig` added.
- `pyproject.toml` — new extras `[ask-claude]`, `[ask-openai]`,
  meta-extra `[ask]`; `[dev]` also pulls in `[ask]`.

Tests: 16 in `tests/test_ask/` covering factory dispatch (all
fallback paths), `--dry-run` rendering + attachment redaction +
no-network guarantee, and the R8 context-provider entry-point
group (opt-in execution, failure isolation).

Commit:
- `30e7545 feat(ask): bcli ask oracle — Claude/OpenAI backends + dry-run + R8 providers`

## Part 3 — `bcli-site/` v0

Status: **shipped** — files only; JSON/YAML parses cleanly. No
`pnpm install` was run (no guaranteed network in this sandbox).

Files:
- `bcli-site/package.json`, `astro.config.mjs`, `tsconfig.json`,
  `tailwind.config.mjs` — Astro 4 + Tailwind 3 stack.
- `bcli-site/src/pages/index.astro` — single page: hero +
  install + 3 example commands (pack install, saved query, ask)
  + features grid + footer with GitHub link.
- `bcli-site/src/components/Hero.astro`, `CodeBlock.astro`,
  `styles/global.css`.
- `bcli-site/public/og.png.placeholder` — TODO note for a
  hand-crafted OG card.
- `bcli-site/README.md` — pnpm dev / pnpm build instructions +
  Vercel deploy note.
- `.github/workflows/site.yml` — Astro build on changes under
  `bcli-site/**`. Vercel deploy step is wired but commented out
  until secrets exist; secrets use `env:` blocks per GitHub's
  injection guidance.
- `.gitignore` — adds `bcli-site/node_modules`, `dist`, `.astro`,
  and lockfiles.

Content compliance with R9: describes shipped features (packs,
ask, MCP server, describe / discovery-first). Does NOT mention
the deferred `bcli agent` mode anywhere on the page.

Commit:
- `44a13f9 feat(site): bcli-site v0 — Astro + Tailwind landing scaffold`

## Final validation snapshot

```
$ .venv/bin/python -m pytest tests/test_context tests/test_packs tests/test_ask -v
75 passed in 0.36s

$ .venv/bin/python -m pytest tests/
906 passed, 5 skipped (full suite — no regressions)

$ .venv/bin/python -m ruff check src/
All checks passed!

$ bcli pack list           # shows both built-in packs
$ bcli pack install starter-generic --dry-run   # 3 fragments + 6 queries + 2 batches
$ bcli ask --dry-run --no-context "test"        # redacted bundle, no network
```

## Out of scope / STUCK

No STUCK files were written — every Part landed within scope. One
late finding from the advisor reconcile pass landed two bug fixes
+ regression tests before "done":

- **`bcli ask --no-context` was leaking last-error** because
  `build_bundle` reads `last-error.json` from disk when no record
  is passed. Added `skip_last_error=True` parameter to the builder
  and threaded it through the CLI. Regression test
  `test_no_context_suppresses_existing_last_error` writes a real
  last-error file, then asserts the phrase appears in the default
  bundle but NOT in the `--no-context` bundle.
- **`bcli ask --include-debug` was wired but inert.** The CLI now
  reads `last-error-debug.json` (mode 0600 sidecar) when the flag
  is set. Regression test `test_include_debug_reads_traceback_sidecar`
  asserts "Traceback" absent by default + present with the flag.

Two follow-up items the next session should pick up:

1. **`bcli describe` excerpt wiring in `bcli ask`.** The `ask`
   command's `--no-context` flag is honoured, but the *default*
   path does not yet subprocess `bcli describe` into the bundle.
   Wiring is straightforward (`subprocess.run(["bcli", "describe",
   "--format", "json"], …)`), gated on `cfg.include_describe`. Left
   as a TODO so the first PR stays focused on the LLM-call surface.

2. **Idempotent pack uninstall on missing-marker** — the installer
   tolerates a missing marker block (warns) but does not force a
   re-walk of the AGENTS.md/CLAUDE.md content to verify the rest of
   the block hasn't been re-edited. Plan reference: R2 lists this
   as the `--force` opt-in path; UX wiring is deferred.

## Wheel build smoke-test

`python -m build --wheel` builds cleanly. Verified that the
hatch `force-include` mapping ships both built-in packs:

```
$ unzip -l dist/bc_cli-0.4.0-py3-none-any.whl | grep _builtin
bcli/packs/_builtin/cronus-demo/pack.yaml
bcli/packs/_builtin/cronus-demo/batches/month-end-cronus.yaml
bcli/packs/_builtin/cronus-demo/fragments/...
bcli/packs/_builtin/starter-generic/pack.yaml
bcli/packs/_builtin/starter-generic/batches/...
bcli/packs/_builtin/starter-generic/fragments/...
bcli/packs/_builtin/starter-generic/queries/...
```

`builtin_packs_dir()` looks at both the repo-root `packs/` (editable
install) and the wheel-shipped `bcli/packs/_builtin/`, so `bcli pack
list` works for users installed via `pip install bc-cli`.

## Recommended follow-up

**Beautech companion plan — see Part 1B in
`/Users/igor/.claude/plans/how-would-the-bcli-zippy-lantern.md`**.

The OSS plan above ships mechanism + two generic packs. The
companion plan turns `bcli-beautech-bootstrap`'s existing assets
(`finance.queries.yaml`, `technical.queries.yaml`,
`workflows/*.batch.yaml`, etc.) into three downstream packs
(`beautech-finance`, `beautech-technical`,
`beautech-customer-360`) registering via the
`bcli.packs` entry-point group, plus a Beautech `bcli.ask
context_provider` for the aviation glossary. Touches only the
private bootstrap repo; the OSS pack/ask machinery is the
extension surface it consumes.
