"""Curated tool definitions for the bcli agent.

Single source of truth, three projections (pydantic-ai / claude-agent-sdk
in-process tools / the existing ``bcli_mcp`` server for codex). The
entries below use the exact command shape ``bcli describe --format json``
emits — ``path`` / ``summary`` / ``positionals`` / ``options`` /
``effects`` — so :meth:`bcli.agent.tools.ToolRegistry.from_describe`
can rebuild the same registry from a live describe payload, and so the
argv-building parity tests against :mod:`bcli_mcp._tool_generator`
hold.

The *summaries* here are the curated overlay: richer, LLM-oriented
descriptions (discovery-first guidance, field-name warnings, examples)
than the terse CLI help describe carries. ``CURATED_OVERLAY`` maps a
command path to its enriched description so the same enrichment applies
when the registry is rebuilt from a live describe payload.

Tiers
-----

* ``READ_PATHS``  — always allowed, no approval gate.
* ``WRITE_PATHS`` — gated by the runtime write gate
  (``disable_writes`` / ``caution == high`` / production target) and
  replaced by the single ``draft_batch`` tool in plan mode.

Interactive commands (``auth login``, ``config init``) are excluded by
construction — the agent tells the human to run them.
"""

from __future__ import annotations

from typing import Any

# ── tier membership (command paths) ───────────────────────────────────

READ_PATHS: frozenset[tuple[str, ...]] = frozenset({
    ("get",),
    ("endpoint", "search"),
    ("endpoint", "info"),
    ("endpoint", "fields"),
    ("company", "list"),
    ("env", "list"),
    ("describe",),
})

WRITE_PATHS: frozenset[tuple[str, ...]] = frozenset({
    ("post",),
    ("patch",),
    ("delete",),
    ("action",),
    ("attach", "upload"),
    ("batch", "run"),
})

# ── curated descriptions (the overlay) ────────────────────────────────

CURATED_OVERLAY: dict[tuple[str, ...], str] = {
    ("get",): (
        "GET records from a Business Central entity. Discovery-first: "
        "if you are not certain the endpoint or a field name exists, "
        "call bcli_endpoint_search / bcli_endpoint_fields first — never "
        "guess field names in --filter or --select. Returns JSON "
        "records. Example: endpoint='vendors', filter=\"displayName eq "
        "'Acme'\", top=5."
    ),
    ("endpoint", "search"): (
        "Fuzzy-search available Business Central endpoints by name "
        "fragment. Use this before bcli_get whenever you are unsure an "
        "endpoint exists or how it is spelled. Returns matching "
        "endpoint names with descriptions."
    ),
    ("endpoint", "info"): (
        "Structured metadata for one endpoint: supported verbs, route, "
        "domain, caution level, key field, and known field names."
    ),
    ("endpoint", "fields"): (
        "Discover the real field names on an endpoint (from registry "
        "metadata, falling back to sampling one record). Call this "
        "before building a --filter or --select expression — BC field "
        "names are camelCase and rarely what you would guess."
    ),
    ("company", "list"): (
        "List the companies (legal entities) available on the active "
        "environment, including configured aliases."
    ),
    ("env", "list"): (
        "List the Business Central environments visible to the active "
        "profile (e.g. production / sandbox)."
    ),
    ("describe",): (
        "Project the active bcli surface: available endpoints, profile "
        "constraints (read-only? category-scoped?), and the resolved "
        "target environment + company. Call this when you need to know "
        "what you are allowed to do."
    ),
    ("post",): (
        "POST (create) a record. data is a JSON object string of the "
        "new record's fields. Gated by write safety: read-only "
        "profiles, high-caution endpoints, and production targets "
        "require explicit operator approval — a refusal result means "
        "the operator declined; do not retry."
    ),
    ("patch",): (
        "PATCH (update) fields on an existing record by id. data is a "
        "JSON object string of only the fields to change. Same write-"
        "safety gating as bcli_post."
    ),
    ("delete",): (
        "DELETE a record by id. Irreversible — prefer asking the "
        "operator before proposing deletes. Same write-safety gating "
        "as bcli_post."
    ),
    ("action",): (
        "Invoke an OData v4 bound action on a record (e.g. "
        "Microsoft.NAV.post on a draft invoice). Bound actions often "
        "post/finalize documents — treat them as high-impact writes."
    ),
    ("attach", "upload"): (
        "Upload a local file as a documentAttachment linked to a parent "
        "record (two-phase BC upload). parent_type examples: 'Job', "
        "'Purchase Invoice'."
    ),
    ("batch", "run"): (
        "Run a multi-step batch YAML file through the bcli batch "
        "runner (dry-run first when unsure). Use for multi-record or "
        "chained writes instead of many single posts."
    ),
}

# ── built-in definitions (describe-shaped) ────────────────────────────


def _cmd(
    path: list[str],
    *,
    effects: list[str],
    positionals: list[dict[str, Any]] | None = None,
    options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "summary": CURATED_OVERLAY.get(tuple(path), ""),
        "positionals": positionals or [],
        "options": options or [],
        "effects": effects,
        "emits_result_envelope": effects == ["mutating"],
    }


BUILTIN_DEFINITIONS: list[dict[str, Any]] = [
    _cmd(
        ["get"],
        effects=["read"],
        positionals=[
            {"name": "endpoint", "type": "str", "required": True},
            {"name": "record_id", "type": "str", "required": False},
        ],
        options=[
            {"name": "--filter", "type": "str"},
            {"name": "--select", "type": "str"},
            {"name": "--expand", "type": "str"},
            {"name": "--orderby", "type": "str"},
            {"name": "--top", "type": "int",
             "limits": {"default": 50, "minimum": 1, "maximum": 1000}},
            {"name": "--skip", "type": "int", "limits": {"minimum": 0}},
            {"name": "--company", "type": "str"},
        ],
    ),
    _cmd(
        ["endpoint", "search"],
        effects=["read"],
        positionals=[{"name": "pattern", "type": "str", "required": True}],
    ),
    _cmd(
        ["endpoint", "info"],
        effects=["read"],
        positionals=[{"name": "name", "type": "str", "required": True}],
    ),
    _cmd(
        ["endpoint", "fields"],
        effects=["read"],
        positionals=[{"name": "name", "type": "str", "required": True}],
    ),
    _cmd(["company", "list"], effects=["read"]),
    _cmd(["env", "list"], effects=["read"]),
    _cmd(["describe"], effects=["read"]),
    _cmd(
        ["post"],
        effects=["mutating"],
        positionals=[{"name": "endpoint", "type": "str", "required": True}],
        options=[
            {"name": "--data", "type": "str", "required": True},
            {"name": "--company", "type": "str"},
        ],
    ),
    _cmd(
        ["patch"],
        effects=["mutating"],
        positionals=[
            {"name": "endpoint", "type": "str", "required": True},
            {"name": "record_id", "type": "str", "required": True},
        ],
        options=[
            {"name": "--data", "type": "str", "required": True},
            {"name": "--etag", "type": "str"},
            {"name": "--company", "type": "str"},
        ],
    ),
    _cmd(
        ["delete"],
        effects=["mutating"],
        positionals=[
            {"name": "endpoint", "type": "str", "required": True},
            {"name": "record_id", "type": "str", "required": True},
        ],
        options=[
            {"name": "--etag", "type": "str"},
            {"name": "--company", "type": "str"},
        ],
    ),
    _cmd(
        ["action"],
        effects=["mutating"],
        positionals=[
            {"name": "endpoint", "type": "str", "required": True},
            {"name": "record_id", "type": "str", "required": True},
            {"name": "action_name", "type": "str", "required": True},
        ],
        options=[
            {"name": "--data", "type": "str"},
            {"name": "--company", "type": "str"},
        ],
    ),
    _cmd(
        ["attach", "upload"],
        effects=["mutating"],
        positionals=[
            {"name": "parent_type", "type": "str", "required": True},
            {"name": "parent_id", "type": "str", "required": True},
            {"name": "file_path", "type": "path", "required": True},
        ],
        options=[
            {"name": "--file-name", "type": "str"},
            {"name": "--company", "type": "str"},
        ],
    ),
    _cmd(
        ["batch", "run"],
        effects=["mutating"],
        positionals=[{"name": "file", "type": "path", "required": True}],
        options=[
            {"name": "--dry-run", "type": "bool"},
            {"name": "--set", "type": "str"},
        ],
    ),
]


# ── plan-mode replacement tool ────────────────────────────────────────

DRAFT_BATCH_TOOL: dict[str, Any] = {
    "path": ["agent", "draft-batch"],
    "summary": (
        "Plan mode is active: direct writes are disabled. Draft the "
        "intended changes as a bcli batch YAML instead. steps is a JSON "
        "array of {name, action: post|patch|delete, endpoint, "
        "record_id?, data?} objects executed in order; later steps can "
        "reference earlier results with ${{ steps.<name>.<field> }}. "
        "The operator reviews the YAML and runs it through 'bcli batch "
        "run' (dry-run first) — nothing is written until then."
    ),
    "positionals": [
        {"name": "name", "type": "str", "required": True},
        {"name": "steps", "type": "str", "required": True},
    ],
    "options": [],
    "effects": ["read"],  # drafting writes nothing
    "emits_result_envelope": False,
}


__all__ = [
    "BUILTIN_DEFINITIONS",
    "CURATED_OVERLAY",
    "DRAFT_BATCH_TOOL",
    "READ_PATHS",
    "WRITE_PATHS",
]
