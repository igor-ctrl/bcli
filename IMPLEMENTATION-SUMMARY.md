# Implementation Summary — `bcli` agent mode (Part 4 of the roadmap)

Branch: `feat/agent-mode`. Implements the approved plan
(`~/.claude/plans/i-attempted-to-create-swift-melody.md`): a Claude Code /
Codex-style interactive agent where an LLM drives bcli's own verbs as tools,
with three backends, a Textual TUI, a first-run wizard, a consent gate,
plan-mode write safety, and per-profile `BC.md` memory.

This continued an interrupted run that had committed a Part-1 skeleton
(`4a71910`) with no tests, extras, or CLI wiring. The skeleton imported cleanly
and matched the plan; it was built on (one real bug fixed) rather than rewritten.

## What was built, per part

### Part 1 — engine + tools + pydantic-ai backend (`3f88923`)

- **Reviewed + corrected the WIP skeleton.** Fixed a latent bug:
  `backends/_pydantic_ai.py` imported `AgentRunResultEvent` from
  `pydantic_ai.messages` (it lives on the top-level `pydantic_ai`); the WIP would
  have `ImportError`ed on the first turn. Verified the corrected backend
  end-to-end against `TestModel`.
- **Engine** (already in the skeleton, validated): `AgentSessionBackend` protocol
  + frozen `AgentEvent` (`_protocol.py`); factory with `NullAgentBackend` fallback
  + one-shot warning (`_factory.py`, mirror of `bcli.ask._factory`); `AgentRuntime`
  with the write-safety approval seam (`_runtime.py`); `ToolRegistry` (read/write
  tiers, plan-mode `draft_batch` swap, `from_describe` rebuild, `bcli_mcp` parity)
  + curated overlay (`tools/_definitions.py`, `_registry.py`); in-process handlers
  with the safety gate enforced inside them (`tools/_impl.py`); three projections
  (`tools/_projections.py`); system-prompt assembly (`_prompt.py`); `BC.md` loader
  (`memory/_bc_md.py`); auth detection (`_auth_detect.py`).
- **Config**: `AgentConfig` wired into `BCConfig` (`config/_model.py`);
  `update_config_section()` for surgical tomlkit writes (`config/_loader.py`).
- **CLI**: wired `bcli agent run` (headless one-shot, streams answer to stdout /
  tool activity to stderr) + `bcli agent init` into `app.py`.
- **pyproject extras**: `agent-local` (`pydantic-ai-slim[anthropic,openai]>=1.107,<2`),
  `agent-claude-code`, `agent-codex`, `agent` meta-extra (+ `textual>=8.2`);
  added to `dev`.
- **repl package**: `repl/__init__.py` (lazy `launch_repl`) + console setup wizard
  (`repl/_wizard.py`).
- **Tests** — `tests/test_agent/` (factory dispatch, registry/parity, safety
  matrix, read handlers, pydantic-ai event stream via `TestModel`, wizard logic,
  consent gate, headless run + plan-mode resolution).

### Part 2 — Textual chat REPL (`05f0bba`)

- **Bare-`bcli` entry**: `app.py` `no_args_is_help=False` +
  `invoke_without_command=True` callback; branch on `ctx.invoked_subcommand is
  None` — dual-TTY → lazy-import REPL, non-TTY → help (regression-tested so
  scripted/piped callers are unaffected). Agent stack never imported for
  subcommands.
- **`repl/_app.py`** — `ChatApp` (Textual): scrolling transcript, streaming
  `MarkdownStream`, `ToolCallPanel` cards, `StatusBar`, modal approval dialog;
  turns run in an exclusive worker consuming `AgentEvent`s; long-lived
  `AsyncBCClient` + `AgentRuntime`; first-run wizard via `run_repl`.
- **`repl/_widgets.py`** — `StatusBar` / `ToolCallPanel` / `ApprovalScreen`
  (y/n + buttons; resolves the runtime gate future).
- **`repl/_commands.py`** — pure slash parser (`/model /profile /company /plan
  /yes /context /clear /help /exit` + aliases).
- **`repl/_plan_mode.py`** — drafted batch YAML → temp file → gated
  `bcli batch run` (dry-run then real), same path `bcli extract` uses.
- **Tests** — `tests/test_repl/` (bare-entry regression, slash parsing, plan-mode
  argv + round-trip, wizard config-write, Textual `App.run_test()` pilots feeding
  canned `AgentEvent` streams for text / tool / approval).
- Also fixed a test side-effect: pinned the text-only pydantic-ai test to
  `call_tools=[]` so it no longer shells out to a real `bcli batch` subprocess
  (that was polluting `test_context`'s last-error read under full-suite ordering).

### Part 3 — Claude Code backend (`8a57726`)

- **`backends/_claude_sdk.py`** — `ClaudeCodeBackend` over `ClaudeSDKClient`.
  bcli verbs become an in-process SDK MCP server from the SAME `_impl.py`
  handlers; `allowed_tools` restricted to `mcp__bcli__*` (built-in coding tools
  never allowed); `can_use_tool` coarse fence on top of the per-handler write
  gate. Handles the documented Python quirk: streaming `AsyncIterable` prompt +
  dummy `PreToolUse` hook returning `{"continue_": True}` so `can_use_tool`
  fires. Translates `AssistantMessage` / `TextBlock` / `ToolUseBlock` /
  `ToolResultBlock` / `ResultMessage` → `AgentEvent`s.
- Consent flow (`repl/_consent.py`, present since the skeleton) covers
  claude-code subscription auth — literal `yes`, persisted; API keys never
  prompt.
- **Tests** — fake `claude_agent_sdk` injected into `sys.modules` (package not
  installed): factory build, event translation, `can_use_tool` fence, dummy
  hook, bcli-only `allowed_tools`.

### Part 4 — Codex backend (`3680517`)

- **`backends/_codex.py`** — `CodexBackend` over the `openai-codex` SDK.
  Codex is an MCP client → `to_mcp_config()` registers the existing `bcli-mcp`
  server (no new tool code; the write gate runs one layer down in the bcli
  subprocess + codex `approval_mode`). `base_instructions` carries bcli's prompt;
  approval escalates to `on_request` under production/plan mode. Notifications
  mapped best-effort to `AgentEvent`s; final answer from `TurnResult`.
- **`[tool.uv] prerelease = "allow"`** so the universal lock resolves the beta's
  pinned prerelease runtime (`openai-codex-cli-bin`); core deps stay stable.
- **Tests** — fake `openai_codex` injected into `sys.modules`: `to_mcp_config`,
  factory build, notification mapping + final answer, `thread_start` config +
  instructions, production approval escalation.

### Docs

`docs/agent.md` (end-to-end guide), `agent` section in
`docs/command-reference.md`, README docs-table entry, and the Agent Mode
architecture section in the (untracked, local) `CLAUDE.md`.

## Commit list (on `feat/agent-mode`, local only — not pushed)

```
<docs> docs(agent): agent mode guide, command reference, README, summary
3680517 feat(agent): Part 4 — Codex backend (openai-codex SDK)
8a57726 feat(agent): Part 3 — Claude Code backend (claude-agent-sdk)
05f0bba feat(agent): Part 2 — Textual chat REPL, bare-bcli entry, plan mode
3f88923 feat(agent): complete Part 1 — engine, tools, pydantic-ai backend, headless run
4a71910 wip(agent): Part 1 engine skeleton  (pre-existing checkpoint)
```

## Test results

- `tests/test_agent/`: **61 passed** (factory, registry/parity, safety matrix,
  read handlers, pydantic-ai stream, wizard, consent, headless run, claude-code
  backend, codex backend).
- `tests/test_repl/`: **22 passed** (bare-entry, slash commands, plan mode,
  wizard write, Textual pilots).
- **Full suite: 1030 passed, 5 skipped** (`uv run pytest tests/`).
- `uv run ruff check src/`: **clean**.

No network in any test: pydantic-ai uses `TestModel`; the claude-agent-sdk and
openai-codex packages (not installed) are faked in `sys.modules`.

## Deviations from the plan (and why)

1. **Codex SDK shape.** The plan assumed `import codex` driving `codex
   app-server` over JSON-RPC (`thread/turn/item` events). The actually-published
   package is `openai-codex` (import `openai_codex`, beta `0.1.0b3`), exposing a
   higher-level `AsyncCodex().thread_start(...) -> thread.turn(input) ->
   AsyncTurnHandle.stream()` + `TurnResult`. I inspected the live PyPI metadata
   and the GitHub `sdk/python/docs/api-reference.md` and targeted the real API.
   Notification → `AgentEvent` mapping is intentionally defensive (attribute
   probing, not isinstance on concrete beta types) since the item/notification
   shape is not yet 1.0-stable; the final answer always arrives via `TurnResult`
   regardless.
2. **`[tool.uv] prerelease = "allow"`** added so the universal lockfile can
   resolve `[agent-codex]` (its runtime `openai-codex-cli-bin` is a pinned
   prerelease). Every other dependency still pins a stable release.
3. **Setup wizard is rich-prompt based, not Textual screens.** It must work
   identically from `bcli agent init` in a bare terminal and from the REPL's
   first-run path; a plain-prompt flow with pure, unit-testable decision logic
   (`detect_backends`, `build_agent_section`) was the simpler, more testable
   choice. The chat itself is full Textual as specified.
4. **`pydantic-ai` backend test** uses `TestModel(call_tools=[...])` rather than a
   `FunctionModel` script — `run_stream_events` requires a `stream_function` for
   `FunctionModel`, whereas `TestModel` streams deterministically and lets us
   pick exactly which tool is called.

## Manual follow-ups (live smoke tests — need real keys / installed CLIs)

These are NOT in the automated suite (no network / no installed `claude` /
`codex` binaries in CI). From the plan's Verification section:

1. `bcli` on a TTY with no `[agent]` config → wizard; configure Ollama (no key)
   → chat opens.
2. BYOK: `[agent] backend=pydantic-ai model=anthropic:claude-sonnet-4-5` → ask
   "how many vendors does LLC have?" → watch the tool panel run `get vendors`,
   streamed answer.
3. Write safety: a `disable_writes=true` sandbox profile → ask the agent to
   create a vendor → approval dialog / plan-mode draft; decline → refusal.
4. Claude Code: a machine with `claude` installed and no `ANTHROPIC_API_KEY` →
   wizard offers it, consent text shown, literal `yes` required, chat works on
   subscription credit. (Requires `pip install "bc-cli[agent-claude-code]"`.)
5. Codex: `codex` installed → backend registers `bcli-mcp`, tool calls
   round-trip, approval policy surfaces writes. (Requires
   `pip install "bc-cli[agent-codex]"`; verify the live notification shape maps
   cleanly — `_notification_to_event` is defensive but unverified against a real
   stream.)
