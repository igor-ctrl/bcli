"""``bcli skill`` — Typer group for skill projection (Phase 6 / Phase 7).

``bcli skill install``
    Generates a slash-command-per-saved-query and slash-command-per-batch
    Markdown bundle suitable for Claude Code at
    ``<target>/.claude/commands/bcli-<name>.md``, plus a top-level
    ``<target>/.claude/skills/bcli/SKILL.md`` index grouped by
    ``categories:``.

    Idempotent — each generated file embeds a ``content_hash:`` over the
    rest of its body; re-runs that produce the same hash are a no-op.
    Hand-edited files with ``manual: true`` in their YAML frontmatter are
    NEVER overwritten.

Worker B (Phase 7) will add ``bcli skill init`` to this group; that's
why we register ``skill`` as a Typer group from the start rather than a
flat command.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console

from bcli.config._defaults import CONFIG_DIR
from bcli_cli._state import state

# Per-profile saved queries (mirror ``query_cmd.QUERIES_DIR``) and
# batch-template directory. Imported via module attrs so tests can
# monkeypatch with ``setattr(... raising=False)``.
QUERIES_DIR = CONFIG_DIR / "queries"
BATCHES_DIR = CONFIG_DIR / "batches"


# `bcli skill` is a single Typer group shared by ``skill_init_cmd``
# (``init`` / ``update`` wizards — PR #19) and this module
# (``install``). PR #19 was approved first and owns the group's
# top-level registration in ``app.py``; we alias its Typer instance
# so ``@app.command("install")`` below attaches to the *same* group.
# This avoids a duplicate ``add_typer(..., name="skill", ...)`` (which
# Typer raises on) and keeps ``bcli skill --help`` listing init,
# update, and install side by side.
from bcli_cli.commands import skill_init_cmd  # noqa: E402

app = skill_init_cmd.app

console = Console()
err = Console(stderr=True)


# ─── Data types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Arg:
    """One CLI argument on a generated slash command."""
    name: str
    type: str = "string"
    required: bool = False
    example: str = ""


@dataclass
class _SlashItem:
    """A single Markdown file to write under .claude/commands/."""
    kind: str              # "query" | "batch"
    name: str              # base name (e.g. "vendor-by-no")
    slug: str              # filename slug (e.g. "bcli-vendor-by-no")
    description: str
    categories: list[str] = field(default_factory=list)
    args: list[_Arg] = field(default_factory=list)
    # Source-specific metadata used in the body template.
    source_path: str = ""  # absolute path of the originating yaml
    batch_relpath: str = ""  # relative path passed to ``bcli batch run``


# ─── YAML parsing helpers (additive — no breaking changes) ─────────


def _coerce_args(raw: Any, params: dict[str, Any] | None = None) -> list[_Arg]:
    """Resolve a query/batch's ``args:`` list, deriving from ``params:`` if absent.

    Order: explicit ``args:`` (manual override) → derive from ``params:``
    keys with required first, optional second, both in YAML insertion
    order. Returns an empty list when neither is present.
    """
    if isinstance(raw, list) and raw:
        out: list[_Arg] = []
        for entry in raw:
            if not isinstance(entry, dict) or "name" not in entry:
                continue
            out.append(_Arg(
                name=str(entry["name"]),
                type=str(entry.get("type") or "string"),
                required=bool(entry.get("required", False)),
                example=str(entry.get("example") or ""),
            ))
        if out:
            return out

    if not isinstance(params, dict):
        return []

    required_first: list[_Arg] = []
    optional_after: list[_Arg] = []
    for key, value in params.items():
        spec = value if isinstance(value, dict) else {}
        arg = _Arg(
            name=str(key),
            type=str(spec.get("type") or "string"),
            required=bool(spec.get("required", False)),
            example=str(spec.get("example") or ""),
        )
        if arg.required:
            required_first.append(arg)
        else:
            optional_after.append(arg)
    return required_first + optional_after


def _coerce_categories(raw: Any) -> list[str]:
    if isinstance(raw, list) and raw:
        return [str(c) for c in raw if str(c).strip()]
    return ["unsorted"]


def _load_queries(profile_name: str) -> dict[str, dict[str, Any]]:
    """Load the active profile's saved-queries YAML; tolerate missing file."""
    f = QUERIES_DIR / f"{profile_name}.yaml"
    if not f.is_file():
        return {}
    try:
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    if not isinstance(raw, dict):
        return {}
    queries = raw.get("queries")
    if not isinstance(queries, dict):
        return {}
    return queries


def _discover_batches(profile_name: str) -> list[tuple[str, Path, dict[str, Any]]]:
    """Find batch templates in ``~/.config/bcli/batches/<profile>/*.yaml``.

    CWD-relative discovery is deliberately *not* implemented in v0.4 —
    documented as a v0.5 follow-up so the surface stays small.
    """
    out: list[tuple[str, Path, dict[str, Any]]] = []
    profile_dir = BATCHES_DIR / profile_name
    if not profile_dir.is_dir():
        return out
    for path in sorted(profile_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(raw, dict):
            continue
        # Use ``name:`` from the YAML if present, else the file stem.
        name = str(raw.get("name") or path.stem)
        out.append((name, path, raw))
    return out


# ─── Build the SlashItem list ───────────────────────────────────────


def _items_from_queries(queries: dict[str, dict[str, Any]]) -> list[_SlashItem]:
    out: list[_SlashItem] = []
    for name, body in queries.items():
        if not isinstance(body, dict):
            continue
        description = str(body.get("description") or name)
        categories = _coerce_categories(body.get("categories"))
        args = _coerce_args(body.get("args"), body.get("params"))
        out.append(_SlashItem(
            kind="query",
            name=name,
            slug=f"bcli-{name}",
            description=description,
            categories=categories,
            args=args,
        ))
    out.sort(key=lambda x: x.name)
    return out


def _items_from_batches(batches: list[tuple[str, Path, dict[str, Any]]]) -> list[_SlashItem]:
    out: list[_SlashItem] = []
    for name, path, raw in batches:
        description = str(raw.get("description") or name)
        categories = _coerce_categories(raw.get("categories"))
        args = _coerce_args(raw.get("args"), raw.get("params"))
        out.append(_SlashItem(
            kind="batch",
            name=name,
            slug=f"bcli-batch-{name}",
            description=description,
            categories=categories,
            args=args,
            source_path=str(path),
            batch_relpath=str(path),
        ))
    out.sort(key=lambda x: x.name)
    return out


# ─── Render templates ───────────────────────────────────────────────


def _argument_hint(args: list[_Arg]) -> str:
    """One-line hint that Claude Code surfaces in slash command pickers."""
    pieces: list[str] = []
    for a in args:
        pieces.append(f"<{a.name}>" if a.required else f"[{a.name}]")
    return " ".join(pieces)


def _command_string_query(item: _SlashItem) -> str:
    """Build the ``bcli q ...`` line for a query slash command."""
    positional = " ".join(
        f"{a.name}=${i + 1}" for i, a in enumerate(item.args)
    )
    if positional:
        return f"bcli q {item.name} {positional} --format json"
    return f"bcli q {item.name} --format json"


def _command_string_batch(item: _SlashItem) -> str:
    """Build the ``bcli batch run ...`` line for a batch slash command."""
    set_clauses = " ".join(
        f"--set {a.name}=${i + 1}" for i, a in enumerate(item.args)
    )
    flag_block = " ".join(filter(None, [
        set_clauses,
        "--format json",
        f"--result-out /tmp/{item.name}-$$.json",
    ]))
    return f"bcli batch run {item.batch_relpath} {flag_block}"


def _render_command_md(item: _SlashItem, *, profile_name: str) -> str:
    """Render the Markdown file body for a single slash command.

    Two-phase: render with NO content_hash line at all, hash that body,
    then splice the ``content_hash: sha256:<digest>`` line into the
    provenance block. The hash is over the body **excluding** the hash
    line — so a consumer can verify integrity by stripping the line
    and rehashing, and the file is stable across re-runs (no timestamp
    churn, no chicken-and-egg).
    """
    body = _render_command_md_body(
        item, profile_name=profile_name, content_hash=None,
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return _render_command_md_body(
        item, profile_name=profile_name, content_hash=digest,
    )


def _render_command_md_body(
    item: _SlashItem, *, profile_name: str, content_hash: str | None,
) -> str:
    hint = _argument_hint(item.args)
    if item.kind == "query":
        cmd_string = _command_string_query(item)
        body_intro = (
            "Run the saved query and emit JSON for an agent to consume.\n"
        )
        usage_args = " ".join(f"<{a.name}>" for a in item.args)
        usage_line = f"/bcli-{item.name}" + (f" {usage_args}" if usage_args else "")
    else:
        cmd_string = _command_string_batch(item)
        body_intro = (
            "Run the batch workflow. ``--result-out`` writes the AIP §Phase 2 "
            "result envelope to a tmp file for the agent to read; ``--format "
            "json`` emits per-step JSON to stdout.\n"
        )
        usage_args = " ".join(f"<{a.name}>" for a in item.args)
        usage_line = f"/{item.slug}" + (f" {usage_args}" if usage_args else "")

    frontmatter = (
        "---\n"
        f"description: {item.description}\n"
        + (f"argument-hint: {hint}\n" if hint else "")
        + "---\n"
    )
    hash_line = (
        f"     content_hash: sha256:{content_hash}\n"
        if content_hash is not None
        else ""
    )
    provenance = (
        "<!-- generated-by: bcli skill install\n"
        f"     profile: {profile_name}\n"
        f"     source: {item.kind}/{item.name}\n"
        f"{hash_line}"
        "     manual edits — set ``manual: true`` in the frontmatter to "
        "protect this file from regeneration. -->\n"
    )
    args_table = ""
    if item.args:
        rows = "\n".join(
            f"| `{a.name}` | {a.type} | "
            f"{'required' if a.required else 'optional'} | "
            f"{a.example or '—'} |"
            for a in item.args
        )
        args_table = (
            "\n## Arguments\n\n"
            "| name | type | required | example |\n"
            "|------|------|----------|---------|\n"
            f"{rows}\n"
        )
    body = (
        f"{frontmatter}\n"
        f"{provenance}\n"
        f"# {item.slug}\n\n"
        f"{item.description}\n\n"
        f"{body_intro}\n"
        "## Usage\n\n"
        f"```\n{usage_line}\n```\n"
        f"{args_table}\n"
        "## Implementation\n\n"
        "```bash\n"
        f"{cmd_string}\n"
        "```\n"
    )
    return body


def _render_index_md(items: list[_SlashItem], *, profile_name: str) -> str:
    """Render the top-level SKILL.md, grouped by category."""
    by_cat: dict[str, list[_SlashItem]] = {}
    for item in items:
        for cat in item.categories or ["unsorted"]:
            by_cat.setdefault(cat, []).append(item)
    # Stable order: alphabetical categories, but ``unsorted`` last.
    ordered_cats = sorted(
        by_cat.keys(),
        key=lambda c: (c == "unsorted", c),
    )

    lines: list[str] = []
    lines.append("---")
    lines.append("name: bcli")
    lines.append("description: bcli slash commands generated from saved queries and batch workflows.")
    lines.append("---")
    lines.append("")
    lines.append("<!-- generated-by: bcli skill install -->")
    lines.append("")
    lines.append(f"# bcli — profile `{profile_name}`")
    lines.append("")
    lines.append(
        "Slash commands and workflow runners projected from your saved "
        "queries (`bcli q`) and batch templates (`bcli batch run`)."
    )
    lines.append("")

    for cat in ordered_cats:
        lines.append(f"## {cat}")
        lines.append("")
        for it in sorted(by_cat[cat], key=lambda x: x.slug):
            lines.append(f"- `/{it.slug}` — {it.description}")
        lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return body + f"\n<!-- content_hash: sha256:{digest} -->\n"


# ─── manual: true frontmatter detection ─────────────────────────────


_MANUAL_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n",
    flags=re.DOTALL,
)


def _is_manual(path: Path) -> bool:
    """True iff the existing file declares ``manual: true`` in frontmatter."""
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    m = _MANUAL_RE.search(text)
    if not m:
        return False
    fm_body = m.group("fm")
    try:
        loaded = yaml.safe_load(fm_body) or {}
    except yaml.YAMLError:
        return False
    return bool(loaded.get("manual", False))


# ─── Atomic write helper ────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ─── Hash-check idempotency ─────────────────────────────────────────


def _existing_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"content_hash:\s*sha256:([0-9a-f]+)", text)
    return m.group(1) if m else None


def _content_hash_of(rendered: str) -> str:
    m = re.search(r"content_hash:\s*sha256:([0-9a-f]+)", rendered)
    return m.group(1) if m else ""


# ─── Target resolution ──────────────────────────────────────────────


def _resolve_target_dir(target: Path | None) -> Path:
    """Resolve where to drop ``.claude/commands/``.

    1. Explicit ``--target`` always wins.
    2. CWD has ``.claude/`` → use CWD (project-local convention).
    3. Otherwise ``~/.claude/`` (user-global; Claude Code default).
    """
    if target is not None:
        return target
    cwd = Path.cwd()
    if (cwd / ".claude").is_dir():
        return cwd
    return Path.home()


# ─── Main command ───────────────────────────────────────────────────


@app.command("install")
def install_command(
    target: Path | None = typer.Option(
        None,
        "--target", "-t",
        help="Project root to write `.claude/commands/` and `.claude/skills/bcli/SKILL.md` into. "
             "Defaults to CWD if it has a `.claude/` dir, else $HOME.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print what would be generated without writing anything.",
    ),
) -> None:
    """Project saved queries + batch templates as Claude Code slash commands.

    Reads the active profile's saved queries (`~/.config/bcli/queries/
    <profile>.yaml`) and batch templates (`~/.config/bcli/batches/
    <profile>/*.yaml`), generates one `.claude/commands/bcli-<name>.md`
    per item, plus a `.claude/skills/bcli/SKILL.md` index grouped by
    `categories:`.

    Idempotent — re-runs are no-ops when the source hasn't changed.
    Files with `manual: true` in their frontmatter are preserved.

    \b
    Examples:
      bcli skill install                 # default target (CWD or $HOME)
      bcli skill install --target ./proj # write into a specific project
      bcli skill install --dry-run       # preview without writing
    """
    profile_name = state.active_profile_name
    target_dir = _resolve_target_dir(target)
    cmds_dir = target_dir / ".claude" / "commands"
    index_path = target_dir / ".claude" / "skills" / "bcli" / "SKILL.md"

    queries = _load_queries(profile_name)
    batches = _discover_batches(profile_name)

    items = _items_from_queries(queries) + _items_from_batches(batches)

    if not items:
        console.print(
            "[yellow]No saved queries or batch templates found for profile "
            f"'{profile_name}'.[/yellow]"
        )
        console.print(
            f"  Add YAML files under [bold]{QUERIES_DIR}/{profile_name}.yaml[/bold]\n"
            f"  or [bold]{BATCHES_DIR}/{profile_name}/[/bold] and re-run."
        )
        return

    written: list[str] = []
    skipped_manual: list[str] = []
    skipped_unchanged: list[str] = []

    for item in items:
        out_path = cmds_dir / f"{item.slug}.md"
        rendered = _render_command_md(item, profile_name=profile_name)

        if _is_manual(out_path):
            skipped_manual.append(item.slug)
            continue

        existing = _existing_hash(out_path)
        target_hash = _content_hash_of(rendered)
        if existing == target_hash and existing:
            skipped_unchanged.append(item.slug)
            continue

        if dry_run:
            written.append(item.slug)
            continue

        _atomic_write(out_path, rendered)
        written.append(item.slug)

    # Index: written last so consumers don't see it before the commands.
    index_body = _render_index_md(items, profile_name=profile_name)
    index_existing = _existing_hash(index_path)
    index_target_hash = _content_hash_of(index_body)
    index_changed = index_existing != index_target_hash
    if index_changed:
        if not dry_run:
            _atomic_write(index_path, index_body)

    _summarise(
        dry_run=dry_run,
        written=written,
        skipped_manual=skipped_manual,
        skipped_unchanged=skipped_unchanged,
        index_changed=index_changed,
        cmds_dir=cmds_dir,
        index_path=index_path,
    )


def _summarise(
    *,
    dry_run: bool,
    written: list[str],
    skipped_manual: list[str],
    skipped_unchanged: list[str],
    index_changed: bool,
    cmds_dir: Path,
    index_path: Path,
) -> None:
    verb = "Would write" if dry_run else "Wrote"
    if written:
        console.print(
            f"[green]{verb}[/green] {len(written)} slash command(s) → "
            f"[bold]{cmds_dir}[/bold]"
        )
        for slug in sorted(written):
            console.print(f"  /{slug}")
    if index_changed:
        console.print(
            f"[green]{verb}[/green] skill index → [bold]{index_path}[/bold]"
        )
    if skipped_unchanged:
        console.print(
            f"[dim]Unchanged ({len(skipped_unchanged)}): "
            f"{', '.join(sorted(skipped_unchanged))}[/dim]"
        )
    if skipped_manual:
        console.print(
            f"[dim]Preserved manual files ({len(skipped_manual)}): "
            f"{', '.join(sorted(skipped_manual))}[/dim]"
        )


__all__ = ["app", "install_command"]
