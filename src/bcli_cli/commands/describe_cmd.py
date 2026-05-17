"""``bcli describe`` — Agent Interface Profile (AIP) v0.1 surface introspection.

Projects the live Typer app + ``EndpointRegistry`` + active ``BCProfile`` as a
single JSON document. This is the canonical artifact consumed by:

* ``bcli_mcp`` (generates tool list dynamically from describe — no hand-written
  schemas drifting from the CLI),
* shell completions and docs,
* any external AI agent that needs to know "what commands and entities does
  this install actually expose for *me*."

The command is designed to tolerate a broken install (no profile configured,
missing registry, etc.) — it's the *first* command an agent runs to figure
out what's even reachable, so it must produce output even when the user
hasn't completed ``bcli config init`` yet. Same self-rescue posture as
``bcli doctor``.

Schema and forward-compat declarations match
`agent-cli-contract-v0.1.md` §Phase 1.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bcli._version import __version__
from bcli_cli._state import state

console = Console()


# Effects mapping — derived from command path. Read commands are the
# default; mutating + other are explicit.
MUTATING_PATHS: set[tuple[str, ...]] = {
    ("post",),
    ("patch",),
    ("delete",),
    ("attach", "upload"),
    ("batch", "run"),
}

# Single command in this set spans multiple steps with durable state —
# Phase 3 (batch ledger) attaches to this declaration.
OPERATION_STATE_PATHS: set[tuple[str, ...]] = {("batch", "run")}

# Read commands by path. Anything not here and not in MUTATING_PATHS
# is treated as "other" (config writes, auth login, registry import, etc.).
READ_PATHS: set[tuple[str, ...]] = {
    ("get",),
    ("q",),
    ("ai-context",),
    ("doctor",),
    ("describe",),
    ("endpoint", "search"),
    ("endpoint", "info"),
    ("endpoint", "fields"),
    ("endpoint", "list"),
    ("endpoint", "show"),
    ("auth", "status"),
    ("auth", "whoami"),
    ("config", "show"),
    ("config", "path"),
    ("config", "list"),
    ("registry", "list"),
    ("registry", "show"),
    ("test",),
    ("test", "endpoint"),
    ("env", "list"),
    ("env", "show"),
    ("company", "list"),
    ("company", "show"),
    ("batch", "list-templates"),
    ("batch", "dry-run"),
    ("attach", "test"),
    ("extract", "list-schemas"),
}


def _describe_cache_path(profile_name: str | None, registry_path: Path | None,
                          profile_path: Path | None) -> Path:
    """Resolve the cache file path for the active profile.

    Read CONFIG_DIR lazily here (not at module load) so tests that
    monkeypatch ``bcli.config._defaults.CONFIG_DIR`` see the override.
    """
    from bcli.config._defaults import CONFIG_DIR

    safe_name = profile_name or "_no_profile_"
    parts: list[str] = [str(__version__)]
    for p in (registry_path, profile_path):
        if p is not None and p.exists():
            parts.append(f"{p}:{p.stat().st_mtime_ns}")
        else:
            parts.append(f"{p}:missing")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return CONFIG_DIR / "describe" / f"{safe_name}.{digest}.json"


def _atomic_write(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` atomically via tempfile + os.replace.

    Cache files must never be observed half-written by a concurrent reader.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ─── Walker: turn the live Typer app into a list of command entries ──


def _classify_effects(path: tuple[str, ...]) -> list[str]:
    if path in MUTATING_PATHS:
        return ["mutating"]
    if path in READ_PATHS:
        return ["read"]
    return ["other"]


def _supported_formats_from_signature(sig: inspect.Signature) -> list[str]:
    """Best-effort: parse ``--format`` help text to extract format names.

    Most ``--format`` options carry a help string of the form
    ``"Output format: table, json, csv, ndjson, raw"``. We split on the
    colon and treat the rest as a comma-separated list. Fallback: ["json"].
    """
    for name, param in sig.parameters.items():
        info = param.default
        param_decls = getattr(info, "param_decls", None) or ()
        if "--format" in param_decls or "-f" in param_decls:
            help_text = getattr(info, "help", "") or ""
            if ":" in help_text:
                tail = help_text.split(":", 1)[1]
                fmts = [t.strip().split()[0] for t in tail.split(",") if t.strip()]
                # Strip parenthetical notes like "(default)".
                fmts = [f.split("(")[0].strip() for f in fmts if f]
                fmts = [f for f in fmts if f.isalnum()]
                if fmts:
                    return fmts
            return ["json"]
    return ["json"]


# Safety bounds the MCP server's auto-generated tools must honour. Keys
# are the (command-path-tuple, long-flag-name); the value rides as the
# option's ``limits`` sub-object in describe's JSON output. This is the
# *one* place that says "an agent must clamp this before invoking" —
# Phase 5's tool generator (``bcli_mcp._tool_generator``) reads it
# straight off describe.
_OPTION_LIMITS: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {
    (("get",), "--top"): {"default": 50, "minimum": 1, "maximum": 1000},
}


def _options_from_signature(
    sig: inspect.Signature, *, path: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for name, param in sig.parameters.items():
        info = param.default
        param_decls = getattr(info, "param_decls", None)
        if not param_decls:
            # Positional argument — handled by ``_positionals_from_signature``.
            continue
        # Pick the longest decl (typically the ``--foo`` form).
        long_name = sorted(param_decls, key=lambda d: (-len(d), d))[0]
        type_name = _annotation_to_name(param.annotation)
        opt: dict[str, Any] = {"name": long_name, "type": type_name}
        # Required options expose ``typer.Option(..., ...)`` — the
        # default is the literal ellipsis. Phase 5's tool generator
        # marks these as required in the JSON Schema so an agent
        # doesn't construct a missing-argument call.
        option_default = getattr(info, "default", None)
        if option_default is Ellipsis:
            opt["required"] = True
        # Crude validator hint — the spec example shows
        # ``validates: "odata-filter"`` for ``--filter``.
        if long_name == "--filter":
            opt["validates"] = "odata-filter"
        # Optional safety-bounds for clamp-on-call (Phase 5 MCP wiring).
        limits = _OPTION_LIMITS.get((path, long_name))
        if limits is not None:
            opt["limits"] = dict(limits)
        options.append(opt)
    return options


def _positionals_from_signature(sig: inspect.Signature) -> list[dict[str, Any]]:
    """List of positional arguments (Typer ``Argument``) per command.

    Each entry has ``{name, type, required}``. ``required=True`` when
    the parameter has no default; ``False`` when it does (Typer renders
    optional positionals via ``typer.Argument(None, ...)``).
    """
    positionals: list[dict[str, Any]] = []
    for name, param in sig.parameters.items():
        info = param.default
        param_decls = getattr(info, "param_decls", None)
        if param_decls:
            # Option / flag — skip.
            continue
        # ``typer.Argument(...)`` instances expose ``default`` on the info
        # object; a missing default (``...``) means required.
        argument_default = getattr(info, "default", None)
        required = argument_default is Ellipsis or argument_default is None
        # ``typer.Argument(None, ...)`` is the idiomatic optional form
        # — default is ``None`` and the function accepts it. Treat as
        # not-required to match the CLI's behaviour.
        if argument_default is None:
            required = False
        positionals.append({
            "name": name,
            "type": _annotation_to_name(param.annotation),
            "required": bool(required),
        })
    return positionals


def _annotation_to_name(ann: Any) -> str:
    """Render a type annotation as a JSON-friendly string.

    ``Optional[int]`` → ``"int"``, ``bool`` → ``"bool"``,
    ``list[str]`` → ``"list[str]"`` (preserved so MCP can branch on it).
    """
    if ann is inspect.Parameter.empty:
        return "string"
    # Strings via from __future__ import annotations.
    if isinstance(ann, str):
        s = ann
    else:
        s = getattr(ann, "__name__", None) or str(ann)
    s = s.replace("Optional[", "")
    # Strip one trailing ``]`` that the Optional unwrap leaves behind.
    if s.endswith("]") and s.count("[") < s.count("]"):
        s = s[:-1]
    # Take the last dotted component but preserve the bracket payload.
    if "[" in s:
        head, rest = s.split("[", 1)
        head = head.split(".")[-1].lower()
        return f"{head}[{rest}".rstrip()
    s = s.split(".")[-1]
    return s.lower() or "string"


def _command_name(cmd_info) -> str:
    """Resolve a Typer CommandInfo's user-facing name.

    Typer falls back to ``callback.__name__`` (underscores → hyphens) when
    no explicit name is given.
    """
    if cmd_info.name:
        return cmd_info.name
    cb = cmd_info.callback
    if cb is None:
        return "<anonymous>"
    return cb.__name__.replace("_", "-")


def _walk_typer(typer_app, parent_path: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Recursively walk a Typer instance, yielding command entries."""
    out: list[dict[str, Any]] = []

    for cmd_info in typer_app.registered_commands:
        name = _command_name(cmd_info)
        path = parent_path + (name,)
        callback = cmd_info.callback
        sig = inspect.signature(callback) if callback else inspect.Signature()
        entry: dict[str, Any] = {
            "path": list(path),
            "summary": _summary_from_callback(callback),
            "options": _options_from_signature(sig, path=path),
            "positionals": _positionals_from_signature(sig),
            "effects": _classify_effects(path),
            "supported_formats": _supported_formats_from_signature(sig),
        }
        # Forward-compat declarations for mutating commands only.
        if entry["effects"] == ["mutating"]:
            entry["emits_result_envelope"] = True
            entry["requires_confirmation"] = "production"
        if path in OPERATION_STATE_PATHS:
            entry["emits_operation_state"] = True
        out.append(entry)

    for group_info in typer_app.registered_groups:
        group_name = group_info.name or (
            group_info.typer_instance.info.name if group_info.typer_instance else "?"
        )
        out.extend(_walk_typer(group_info.typer_instance, parent_path + (group_name,)))

    return out


def _summary_from_callback(callback) -> str:
    if callback is None or not callback.__doc__:
        return ""
    doc = inspect.cleandoc(callback.__doc__)
    # First paragraph only (one-line summary in the JSON projection).
    return doc.split("\n\n", 1)[0].strip().splitlines()[0] if doc else ""


# ─── Registry & profile-constraint projections ────────────────────────


def _project_registry(registry, *, tier_2_enabled: bool) -> dict[str, Any]:
    endpoints: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for meta in registry.list_all():
        tier = "custom" if meta.is_custom else "standard"
        key = (meta.entity_set_name, tier)
        if key in seen:
            continue
        seen.add(key)
        endpoints.append({
            "entity": meta.entity_set_name,
            "tier": tier,
            "domain": getattr(meta, "domain", "standard"),
            "ops": list(meta.supports),
            "fields_known": len(meta.field_names) if meta.field_names else 0,
            "fields_discovered_at": getattr(meta, "fields_discovered_at", None),
        })
    endpoints.sort(key=lambda e: (e["tier"], e["entity"]))
    return {
        "tier_1_custom_count": registry.custom_count,
        "tier_2_standard_enabled": tier_2_enabled,
        "endpoints": endpoints,
    }


def _project_profile_constraints(profile) -> dict[str, Any]:
    if profile is None:
        return {
            "disable_writes": None,
            "disable_standard_api": None,
            "allowed_categories": None,
        }
    return {
        "disable_writes": bool(profile.disable_writes),
        "disable_standard_api": bool(profile.disable_standard_api),
        "allowed_categories": list(profile.allowed_categories) if profile.allowed_categories else None,
    }


# ─── Build the full describe payload ──────────────────────────────────


def _resolve_paths(profile_name: str | None) -> tuple[Path | None, Path | None]:
    """Return (registry_path, config_path) for cache-key + invalidation.

    Both may be ``None`` if the install is incomplete.
    """
    from bcli.config._defaults import CONFIG_FILE, REGISTRIES_DIR

    registry_path = (REGISTRIES_DIR / f"{profile_name}.json") if profile_name else None
    config_path = CONFIG_FILE
    return registry_path, config_path


def _build_payload() -> dict[str, Any]:
    """Project the live app + registry + profile into the JSON shape."""
    # Lazy: avoid circular import at module load.
    from bcli_cli.app import app

    profile_name: str | None = None
    profile = None
    registry = None
    tier_2_enabled = True

    try:
        profile_name = state.active_profile_name
        profile = state.profile
        registry = state.registry
        tier_2_enabled = not bool(profile.disable_standard_api)
    except Exception:  # noqa: BLE001
        # Broken/missing config — agent-friendly: keep going with stubs.
        from bcli.registry._registry import EndpointRegistry

        profile_name = None
        profile = None
        try:
            registry = EndpointRegistry()
        except Exception:  # noqa: BLE001
            registry = None
        tier_2_enabled = True

    commands = _walk_typer(app)
    commands.sort(key=lambda c: tuple(c["path"]))

    if registry is None:
        registry_projection: dict[str, Any] = {
            "tier_1_custom_count": 0,
            "tier_2_standard_enabled": tier_2_enabled,
            "endpoints": [],
        }
    else:
        registry_projection = _project_registry(registry, tier_2_enabled=tier_2_enabled)

    from bcli.exit_codes import EXIT_CODES

    return {
        "version": "0.1",
        "tool": "bcli",
        "tool_version": __version__,
        "profile": profile_name,
        "commands": commands,
        "registry": registry_projection,
        "profile_constraints": _project_profile_constraints(profile),
        # AIP §Phase 4a — project the documented exit-code taxonomy so an
        # agent runtime can render a meaningful error from any non-zero
        # bcli exit.
        "exit_codes": {str(code): label for code, label in EXIT_CODES.items()},
    }


# ─── Cached payload helpers ───────────────────────────────────────────


def _load_or_build_payload() -> dict[str, Any]:
    """Read from cache if fresh, otherwise regenerate + write.

    Cache key is sha256 of registry mtime + profile mtime + tool version.
    A new key means a new file; the old file is left behind harmlessly
    (a future ``bcli describe clean`` could prune them).
    """
    profile_name = state.profile_name or (
        state._config.defaults.profile if state._config is not None else None
    )
    if profile_name is None:
        try:
            profile_name = state.active_profile_name
        except Exception:  # noqa: BLE001
            profile_name = None

    registry_path, profile_path = _resolve_paths(profile_name)
    cache_path = _describe_cache_path(profile_name, registry_path, profile_path)
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass  # fall through to regenerate

    payload = _build_payload()
    try:
        _atomic_write(cache_path, json.dumps(payload, indent=2, sort_keys=False))
    except OSError:
        # Cache failure must not crash describe; the payload is still valid.
        pass
    return payload


# ─── Subtree slicing ──────────────────────────────────────────────────


def _slice_subtree(payload: dict[str, Any], path: list[str]) -> dict[str, Any]:
    """Return the entry whose ``path`` matches, wrapped in a small envelope.

    Drops ``registry`` and ``profile_constraints`` — agents calling
    ``bcli describe get`` want minimum tokens, not the full surface.
    """
    target = tuple(path)
    matches = [c for c in payload["commands"] if tuple(c["path"]) == target]
    if not matches:
        # Also allow a prefix match for groups (e.g. `bcli describe attach`).
        matches = [c for c in payload["commands"] if tuple(c["path"])[: len(target)] == target]
    if not matches:
        # Surface a clear error and exit non-zero so an agent can react.
        console.print(f"[red]No command matches path: {' '.join(path)!r}[/red]")
        raise typer.Exit(4)

    return {
        "version": payload["version"],
        "tool": payload["tool"],
        "tool_version": payload["tool_version"],
        "profile": payload["profile"],
        "commands": matches,
    }


# ─── Output rendering ─────────────────────────────────────────────────


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=False))


def _emit_table(payload: dict[str, Any]) -> None:
    table = Table(title=f"bcli describe (profile={payload['profile']!r})")
    table.add_column("Command")
    table.add_column("Effects")
    table.add_column("Formats")
    for cmd in payload["commands"]:
        table.add_row(
            " ".join(cmd["path"]),
            ",".join(cmd["effects"]),
            ",".join(cmd["supported_formats"]),
        )
    console.print(table)
    if "registry" in payload:
        reg = payload["registry"]
        console.print(
            f"Registry: tier_1_custom_count={reg['tier_1_custom_count']}"
            f" tier_2_standard_enabled={reg['tier_2_standard_enabled']}"
            f" endpoints={len(reg['endpoints'])}"
        )


# ─── Typer entry point ────────────────────────────────────────────────


def describe_command(
    command_path: list[str] = typer.Argument(
        None,
        help="Optional command path (e.g. 'get' or 'batch run') for a narrow subtree",
    ),
    format: str = typer.Option(
        "json",
        "--format",
        "-f",
        help="Output format: json (default, agent-friendly), table (human summary)",
    ),
) -> None:
    """Project the live CLI surface + registry + profile as JSON.

    Designed for AI agents: one canonical artifact that MCP, completions,
    and docs all consume. Tolerates broken installs (no profile, missing
    registry) so it works as the *first* command an agent runs.

    \b
    Examples:
      bcli describe                       full surface, JSON
      bcli describe --format table        compact human summary
      bcli describe get                   subtree for the `get` command
      bcli describe batch run             subtree for `batch run`
    """
    payload = _load_or_build_payload()

    if command_path:
        payload = _slice_subtree(payload, command_path)

    fmt = (format or "json").lower()
    if fmt == "table":
        _emit_table(payload)
    else:
        _emit_json(payload)
