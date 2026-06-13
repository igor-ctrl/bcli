# Agent Mode — the `bcli` chat REPL

Agent mode turns `bcli` into an interactive assistant for Business Central: an
LLM drives bcli's own verbs as tools, so you ask questions in plain language and
watch it run `get`, `endpoint search`, `post`, and friends — with the same write
safety the CLI enforces.

```
$ bcli
  bcli agent — model: anthropic:claude-sonnet-4-5 · profile: finance · env: sandbox

› how many vendors does LLC have?
  → bcli_get {"endpoint": "vendors", "company": "LLC", "top": 1, "count": true}
  ✓ bcli_get
  LLC has 312 vendors.
```

Bare `bcli` on an interactive terminal launches the chat. Piped or scripted
(`echo … | bcli`, `bcli | cat`) it still prints help — existing automation is
unaffected.

## Quick start

```bash
# Install the agent extra (BYOK loop + the Textual TUI):
uv tool install -e ".[agent]" --force      # or: pip install "bc-cli[agent]"

# First launch runs a setup wizard (also: bcli agent init):
bcli
```

The wizard asks which LLM to use, stores any API key in your OS keychain, writes
the `[agent]` config section, and drops you into chat.

## Backends (BYOK or bring your own CLI)

| `[agent] backend` | What it is | Extra |
|---|---|---|
| `pydantic-ai` | In-process loop — any Anthropic / OpenAI / local OpenAI-compatible model (Ollama, vLLM, LM Studio) via `provider:model` strings + `base_url`. The default BYOK path. | `[agent]` / `[agent-local]` |
| `claude-code` | Drives your installed Claude Code through the Claude Agent SDK; bcli's verbs become an in-process MCP server. | `[agent-claude-code]` |
| `codex` | Drives your installed Codex CLI through the `openai-codex` SDK; Codex consumes bcli's existing `bcli-mcp` server. | `[agent-codex]` |
| `null` (default) | No backend; the REPL prints a setup hint. | — |
| `my_pkg.module:MyBackend` | Any class implementing `bcli.agent.AgentSessionBackend` with a `from_config` classmethod. | your own |

All three first-party backends emit the **same** `AgentEvent` stream, so the
chat UI, the write-safety gate, and plan mode behave identically regardless of
which one you pick. Switching is a one-line config change.

### `[agent]` config

```toml
[agent]
backend = "pydantic-ai"                 # null | pydantic-ai | claude-code | codex | module:Class
model = "anthropic:claude-sonnet-4-5"   # provider:model (pydantic-ai); bare name → OpenAI-compatible
api_key_env = "ANTHROPIC_API_KEY"       # optional override of the key env var
base_url = ""                           # Ollama / OpenAI-compatible endpoint
max_steps = 20                          # tool-call budget per turn
memory = true                           # load per-profile BC.md into the prompt
plan_mode_default = "auto"              # auto (on for production) | on | off
```

### Local models (no API key)

```toml
[agent]
backend = "pydantic-ai"
model = "ollama:llama3.1"
base_url = "http://localhost:11434/v1"
```

## Credentials

Resolution order matches the rest of bcli (`bcli.auth._credentials`): explicit
`api_key_env` → OS keychain (service `bcli`, key `llm:<provider>`) → the
provider's default env var (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). The wizard
writes keys to the keychain; nothing sensitive lands in the config file.

### Subscription auth + the consent gate

`claude-code` and `codex` can ride your personal Claude / ChatGPT subscription
instead of an API key. That's individual-use territory at both vendors
(Anthropic's per-plan Agent SDK credit is sized for one person; Codex
subscription access uses an undocumented endpoint with rate windows). So bcli
**never defaults to it**: when a subscription login is the only credential, the
first run shows an explicit notice and requires you to type literal `yes`.
Consent is persisted as `subscription_authorized = true` + a timestamp under
`[agent]` — visible in plain text, revocable by deleting the line. **Teams
should use API keys**, which never prompt.

## Write safety

Writes are gated **inside the tool implementations**, never just in the prompt:

- `disable_writes = true` profiles, `caution: high` endpoints, and **production**
  targets pause the write and raise an approval dialog (or `--yes` in headless
  mode). Decline → the model gets a typed refusal and is told not to retry.
- Every approved write goes through `SafeContext` with an explicit
  environment + company.
- **Plan mode** (default ON for production): the write tier is replaced by a
  single `draft_batch` tool. The agent proposes a `bcli batch` YAML; you review
  it, then it's promoted through the normal gated `bcli batch run` path
  (dry-run first) — exactly like `bcli extract`. Toggle with `/plan`.

## Chat commands

| Command | Effect |
|---|---|
| `/model [name]` | Show / note a model switch (persist via config) |
| `/profile [name]` | Switch the bcli profile (re-resolves env, company, registry) |
| `/company <alias>` | Set the default company for tool calls |
| `/plan` | Toggle plan mode |
| `/yes` | Approve a pending write |
| `/context` | Show the resolved profile / env / plan-mode context |
| `/clear` | Clear the transcript |
| `/help` | List commands |
| `/exit` (`/quit`, Ctrl+C) | Leave the chat |

## Memory (BC.md)

When `memory = true`, agent mode loads a `BC.md` file into the system prompt
after the base instructions: a project-local `./BC.md` (discovered by walking up
from the working directory) wins over the per-profile
`~/.config/bcli/profiles/<profile>/BC.md`. Use it to pin durable context — "our
vendors are keyed by `displayName`, never by number". Read-only in v1.

## Headless one-shot

```bash
bcli agent run "how many open sales orders are there?"
bcli agent run "draft a vendor for Acme" --plan          # force plan mode
bcli agent run "…" --yes                                  # auto-approve writes (scripted; careful)
```

`bcli agent run` streams the answer to stdout and tool activity to stderr —
testable without a TTY and the same engine the chat REPL uses.

## Architecture (engine / renderer split)

The seam between bcli and the LLM is the **session**, not the model call.
`src/bcli/agent/` is the SDK engine: a backend implements
`AgentSessionBackend` and streams uniform `AgentEvent`s (`text_delta`,
`tool_call_started`, `tool_result`, `awaiting_approval`, `turn_complete`,
`error`). `src/bcli_cli/repl/` is one renderer (the Textual app); the headless
`bcli agent run` printer is another. The engine never imports `bcli_cli`.

Tools come from a single source — the same `bcli describe --format json` payload
`bcli_mcp` consumes — projected three ways: in-process pydantic-ai tools, an
in-process Claude SDK MCP server, and (for codex) the existing `bcli-mcp`
subprocess. All paths share the handlers in `src/bcli/agent/tools/_impl.py`, so
write safety lives in exactly one place.

## Verification smoke tests (need real keys / binaries)

These aren't in the automated suite (no network / no installed CLIs in CI):

1. `bcli` on a TTY with no `[agent]` → wizard; configure Ollama → chat opens.
2. BYOK: `[agent] backend=pydantic-ai model=anthropic:claude-sonnet-4-5` → ask
   "how many vendors does LLC have?" → watch the tool panel run `get vendors`.
3. Write safety: a `disable_writes=true` sandbox profile → ask the agent to
   create a vendor → approval dialog (or plan-mode draft); decline → refusal.
4. Claude Code: a machine with `claude` installed and no `ANTHROPIC_API_KEY` →
   wizard offers it, consent text shown, literal `yes` required.
5. Codex: `codex` installed → backend registers `bcli-mcp`, tool calls
   round-trip, approval policy surfaces writes.
