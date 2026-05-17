"""``bcli skill init`` — Phase 7 mechanism (AIP v0.1 §Phase 8).

Per-user setup wizard that consumes ``bcli describe`` and writes a
right-sized Claude Code skill bundle plus optional new saved-query
entries. **Mechanism only** — the OSS package ships no Beautech-specific
role templates. Third-party packages (``bcli-beautech-bootstrap``) plug
in via the ``bcli.skill_init.role_templates`` entry-point group.

Hard guarantees this module enforces:

* Reads ``bcli describe`` via subprocess; never imports the CLI's
  internals. The wizard works against any installed bcli version that
  emits the AIP §Phase 1 schema.
* Writes ONLY under ``~/.config/bcli/queries/`` (append-only edits) and
  ``~/.claude/skills/bcli-<user>/`` (atomic file replacement). Any other
  destination raises :class:`SkillInitError` *before* the file is opened.
* New saved-query proposals require explicit per-query ``[y/N]``
  approval. Nothing is written without the operator's consent.
* Provenance frontmatter on every generated file so a later
  ``bcli skill update`` can find and regenerate just its own work.
* Atomic rollback: writes are staged in memory; if any commit step
  fails, no partial file lands.
* Idempotent: an interview-state cache at
  ``~/.config/bcli/skills/.last-init.json`` lets ``bcli skill update
  --non-interactive`` replay the prior interview unchanged. A schema
  drift (different describe ``version`` or registry hash) refuses the
  silent replay and asks for an interactive re-run.

The wizard is the first AIP consumer in bcli's own repo — proof the
contract works for a real downstream tool.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Sequence

import typer
import yaml
from rich.console import Console
from rich.prompt import Confirm, Prompt


app = typer.Typer(no_args_is_help=True, help="Generate a per-user bcli skill bundle")
console = Console()
_stderr = Console(stderr=True)


PROVENANCE_VERSION = "0.1"
_ENTRYPOINT_GROUP = "bcli.skill_init.role_templates"
_STATE_CACHE_FILENAME = ".last-init.json"


# ─── Errors ───────────────────────────────────────────────────────────


class SkillInitError(Exception):
    """Raised by the wizard's pre-flight checks and atomic commit phase.

    Two callers expect this exception:

    * Tests, which pattern-match the message.
    * The Typer entry points, which translate it to ``typer.Exit(1)`` with
      a user-friendly stderr render.
    """


# ─── Path resolution + allow-list ─────────────────────────────────────


def _config_dir() -> Path:
    """``~/.config/bcli`` resolved from ``Path.home()``."""
    return Path.home() / ".config" / "bcli"


def _queries_dir() -> Path:
    return _config_dir() / "queries"


def _skills_root() -> Path:
    """``~/.claude/skills`` — the directory Claude Code reads skill
    bundles from. We write into a ``bcli-<user>/`` subdir."""
    return Path.home() / ".claude" / "skills"


def _state_cache_path() -> Path:
    return _config_dir() / "skills" / _STATE_CACHE_FILENAME


def _user_skill_dir() -> Path:
    """``~/.claude/skills/bcli-<user>/`` — the only place the wizard
    writes Markdown."""
    return _skills_root() / f"bcli-{getpass.getuser()}"


def _assert_writable(path: Path) -> None:
    """Refuse any path outside the wizard's allow-list.

    Allow-list has three roots:

    * ``~/.config/bcli/queries/`` — user-facing saved queries.
    * ``~/.claude/skills/bcli-*/`` — Claude Code skill bundles.
    * ``~/.config/bcli/skills/`` — wizard bookkeeping (state cache for
      ``skill update``). Not user content; the directory is wizard-owned.

    Resolves both ``path`` and the allow-list roots without requiring
    the files to exist (``Path.resolve(strict=False)``) so the check
    runs before any directory has been created.
    """
    resolved = Path(path).resolve()
    queries_root = _queries_dir().resolve()
    skills_root = _skills_root().resolve()
    wizard_state_root = (_config_dir() / "skills").resolve()

    if resolved.is_relative_to(queries_root):
        return
    if resolved.is_relative_to(wizard_state_root):
        # Wizard's own bookkeeping directory. Only the cache file lands
        # here; user content never does.
        return
    if resolved.is_relative_to(skills_root):
        # Within skills, only ``bcli-*`` subdirs are allowed.
        parts_after = resolved.relative_to(skills_root).parts
        if parts_after and parts_after[0].startswith("bcli-"):
            return
    raise SkillInitError(
        f"refusing to write to {resolved} — outside the bcli skill-init "
        "allow-list (queries dir, bcli-* skill dirs, wizard state dir)"
    )


# ─── Describe loader ──────────────────────────────────────────────────


def _load_describe_payload(profile: str | None = None) -> dict[str, Any]:
    """Subprocess ``bcli describe --format json`` and return parsed JSON.

    Mirrors :func:`bcli_mcp._server._load_describe_payload` but lives
    here so the skill wizard doesn't depend on the optional ``[mcp]``
    extra. Production calls this; tests monkeypatch it directly.
    """
    argv = ["bcli"]
    if profile:
        argv.extend(["--profile", profile])
    argv.extend(["describe", "--format", "json"])
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=60.0, check=False,
    )
    if proc.returncode != 0:
        raise SkillInitError(
            f"bcli describe exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '').strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SkillInitError(f"bcli describe returned non-JSON: {exc}") from exc


def _payload_hash(payload: dict[str, Any]) -> str:
    """sha256 over the describe payload — drives the idempotency check.

    Sorting keys keeps the hash stable across describe runs that may
    reorder dict members.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


# ─── Interview state ──────────────────────────────────────────────────


@dataclass(frozen=True)
class InterviewState:
    """Captured interview answers. Frozen so it round-trips cleanly
    through the idempotency cache without accidental mutation."""

    role: str
    top_three: str
    style: str  # "flat" | "meta" | "both"
    generate_new: bool


@dataclass(frozen=True)
class ProposedQuery:
    """A new saved-query proposal awaiting operator approval."""

    name: str
    body: dict[str, Any]


@dataclass(frozen=True)
class SurfacedSlashCommand:
    """An existing saved query the wizard chose to surface for this user."""

    query_name: str
    description: str
    endpoint: str


@dataclass(frozen=True)
class SkillPlan:
    """The complete set of writes the wizard is about to commit.

    Kept frozen so we can compute the plan, run safety checks, then
    commit — and a failure in the commit phase doesn't leave the plan
    object in a half-mutated state.
    """

    profile: str
    describe_version: str
    payload_hash: str
    interview: InterviewState
    slash_commands: tuple[SurfacedSlashCommand, ...]
    approved_new_queries: tuple[ProposedQuery, ...]
    generated_at: str
    target_skills_dir: Path


# ─── Saved-queries discovery ──────────────────────────────────────────


def _load_existing_queries(profile: str) -> dict[str, dict[str, Any]]:
    """Read ``~/.config/bcli/queries/<profile>.yaml`` if present.

    Returns ``{}`` when the file doesn't exist or is empty so the
    wizard's projection step degrades to "no slash commands surfaced"
    rather than crashing.
    """
    path = _queries_dir() / f"{profile}.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    queries = data.get("queries", {})
    if not isinstance(queries, dict):
        return {}
    return queries


def _surface_queries_for_interests(
    queries: dict[str, dict[str, Any]],
    top_three: str,
) -> tuple[SurfacedSlashCommand, ...]:
    """Pick the saved queries that fuzzy-match the user's free-text top-3.

    No role-keyed logic — that would smuggle Beautech-specific
    affinities into the OSS package. We tokenise the top-3 phrase on
    whitespace + commas, then take any query whose ``description``,
    ``name``, or ``endpoint`` contains the token (or has a SequenceMatcher
    similarity ≥ 0.6 to it).
    """
    needles = {
        tok.strip().lower()
        for chunk in top_three.split(",")
        for tok in chunk.split()
        if tok.strip()
    }
    surfaced: list[SurfacedSlashCommand] = []
    seen: set[str] = set()
    for name, body in sorted(queries.items()):
        if name in seen:
            continue
        hay = " ".join([
            name.lower(),
            str(body.get("description", "")).lower(),
            str(body.get("endpoint", "")).lower(),
        ])
        match = False
        for needle in needles:
            if needle and needle in hay:
                match = True
                break
            # Substring miss → fuzzy.
            ratio = SequenceMatcher(None, needle, hay).ratio()
            if needle and ratio >= 0.6:
                match = True
                break
        if match:
            surfaced.append(SurfacedSlashCommand(
                query_name=name,
                description=str(body.get("description", "")),
                endpoint=str(body.get("endpoint", "")),
            ))
            seen.add(name)
    return tuple(surfaced)


# ─── Role-template proposer (entry-point dispatch) ─────────────────────


def _default_role_template_proposer(
    interview: InterviewState, payload: dict[str, Any],
) -> list[ProposedQuery]:
    """OSS default: no proposals.

    Beautech (or any third party) plugs in via the
    ``bcli.skill_init.role_templates`` entry-point group — each provider
    is a callable with the same signature returning a list of
    :class:`ProposedQuery`. The OSS mechanism stays opinion-free; role
    content lives where it belongs (downstream).
    """
    return []


def _collect_proposed_new_queries(
    interview: InterviewState, payload: dict[str, Any],
) -> list[ProposedQuery]:
    """Run every registered role-template provider and concatenate.

    Discovers providers via ``importlib.metadata.entry_points`` so the
    OSS package needs zero knowledge of which downstream packages exist.
    Errors loading individual providers are logged and skipped — one
    broken plugin must not break the wizard.
    """
    proposals: list[ProposedQuery] = []
    # OSS default first (empty list today).
    proposals.extend(_default_role_template_proposer(interview, payload))

    try:
        from importlib.metadata import entry_points
        eps = entry_points(group=_ENTRYPOINT_GROUP)
    except Exception:  # pragma: no cover — defensive
        eps = []

    for ep in eps:
        try:
            provider: Callable[[InterviewState, dict[str, Any]], list[ProposedQuery]]
            provider = ep.load()
            proposals.extend(provider(interview, payload))
        except Exception as exc:  # noqa: BLE001
            _stderr.print(
                f"[yellow]skill init: provider '{ep.name}' failed "
                f"({exc}); skipping.[/yellow]"
            )
    return proposals


# ─── Interview prompts ────────────────────────────────────────────────


def _interview_interactively() -> InterviewState:
    """Ask the four contract-doc-mandated questions via Rich prompts."""
    role = Prompt.ask(
        "Role (finance / ops / aviation / sales / dev / custom)",
        default="custom",
    )
    top_three = Prompt.ask(
        "Top three things you ask BC about daily (free text, comma-separated)",
        default="",
    )
    style = Prompt.ask(
        "Slash command style — flat / meta / both",
        default="flat",
    )
    generate_new = Confirm.ask(
        "Generate suggested NEW queries from your role?",
        default=False,
    )
    return InterviewState(
        role=role.strip(),
        top_three=top_three.strip(),
        style=style.strip(),
        generate_new=bool(generate_new),
    )


def _approve_each_proposal(
    proposed: Sequence[ProposedQuery],
) -> tuple[ProposedQuery, ...]:
    """Show each proposal and require explicit ``y/N`` per query."""
    approved: list[ProposedQuery] = []
    for q in proposed:
        console.print(
            f"[bold]Proposed new query:[/bold] {q.name}\n"
            f"  description: {q.body.get('description', '')}\n"
            f"  endpoint:    {q.body.get('endpoint', '')}\n"
        )
        if Confirm.ask(f"Approve '{q.name}'?", default=False):
            approved.append(q)
    return tuple(approved)


# ─── Provenance helpers ───────────────────────────────────────────────


def _provenance_dict(plan: SkillPlan) -> dict[str, Any]:
    return {
        "generated-by": "bcli skill init",
        "version": PROVENANCE_VERSION,
        "profile": plan.profile,
        "role": plan.interview.role,
        "generated_at": plan.generated_at,
        "source_hash": plan.payload_hash,
    }


def _render_skill_md(plan: SkillPlan) -> str:
    """Compose the SKILL.md text for this user.

    Frontmatter is YAML-formatted (Claude Code reads it that way). The
    body is a short, role-keyed orientation: which existing saved
    queries the user picked + a discovery-first prose pointer to
    ``bcli describe`` so the agent can drill deeper without our
    enumerating every entity.
    """
    meta_yaml = yaml.safe_dump(
        _provenance_dict(plan), sort_keys=False, default_flow_style=False,
    ).strip()
    lines = [
        "---",
        meta_yaml,
        "---",
        "",
        f"# bcli — {plan.interview.role} skill bundle",
        "",
        "Personalised entry-points for this operator's daily workflow. "
        "Generated by `bcli skill init` from the live describe surface.",
        "",
    ]
    if plan.slash_commands:
        lines.append("## Existing saved queries surfaced for you")
        lines.append("")
        for sc in plan.slash_commands:
            line = f"- `bcli q {sc.query_name}`"
            if sc.description:
                line += f" — {sc.description}"
            if sc.endpoint:
                line += f" _(endpoint: `{sc.endpoint}`)_"
            lines.append(line)
        lines.append("")
    if plan.approved_new_queries:
        lines.append("## New queries you approved")
        lines.append("")
        for q in plan.approved_new_queries:
            desc = q.body.get("description", "")
            lines.append(f"- `bcli q {q.name}` — {desc}")
        lines.append("")
    lines.extend([
        "## Discovering more",
        "",
        "Run `bcli describe --format json` to see the full surface, or",
        "`bcli q` (no args) for the current saved-query catalog.",
        "",
    ])
    return "\n".join(lines) + "\n"


# ─── Atomic commit ────────────────────────────────────────────────────


def _stage_queries_file_update(
    plan: SkillPlan,
) -> tuple[Path, str] | None:
    """Build the new ``queries.yaml`` text without writing it.

    Reads the existing file, appends each approved new query with an
    inline ``provenance`` block, and returns ``(path, serialised_text)``.
    Returns ``None`` when there are no new queries to commit.
    """
    if not plan.approved_new_queries:
        return None
    path = _queries_dir() / f"{plan.profile}.yaml"
    existing = _load_existing_queries(plan.profile)
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    queries = dict(existing)
    for q in plan.approved_new_queries:
        body = dict(q.body)
        body["provenance"] = _provenance_dict(plan)
        queries[q.name] = body
    raw["queries"] = queries
    text = yaml.safe_dump(raw, sort_keys=False, default_flow_style=False)
    return path, text


def _atomic_write(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` via tmp + os.replace.

    Mirrors :func:`bcli.result_envelope.write_envelope`'s atomicity so a
    SIGKILL between write and rename never leaves a half-written file
    where Claude Code would read it.
    """
    _assert_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _commit_plan(plan: SkillPlan) -> None:
    """Stage every write, validate paths, then commit atomically.

    All targets are validated by :func:`_assert_writable` *before* any
    file is written, so a guardrail violation aborts cleanly with no
    partial state. Roll-back semantics: each staged write only happens
    after every other write succeeds; if one fails, prior writes are
    reverted to their pre-commit content (or deleted if they didn't
    exist before).
    """
    # 1. Build the in-memory plan.
    queries_change = _stage_queries_file_update(plan)
    skill_md_path = plan.target_skills_dir / "SKILL.md"
    skill_md_text = _render_skill_md(plan)
    state_cache_path = _state_cache_path()
    # Persist both the interview AND the approved-query bodies so a
    # later ``bcli skill update --non-interactive`` replays the same
    # writes (the operator already approved them — re-asking would
    # break the idempotency contract documented in §Phase 8).
    state_cache_text = json.dumps({
        "describe_payload_hash": plan.payload_hash,
        "describe_version": plan.describe_version,
        "profile": plan.profile,
        "interview": asdict(plan.interview),
        "approved_new_queries": [
            {"name": q.name, "body": q.body}
            for q in plan.approved_new_queries
        ],
        "generated_at": plan.generated_at,
    }, indent=2)

    # 2. Pre-flight: every destination must be in the allow-list. All
    # three writes (queries YAML, SKILL.md, state cache) are checked
    # before any file is opened so a guardrail violation aborts cleanly.
    _assert_writable(skill_md_path)
    _assert_writable(state_cache_path)
    if queries_change is not None:
        _assert_writable(queries_change[0])

    # 3. Compute rollback snapshot.
    snapshots: dict[Path, str | None] = {}
    targets: list[tuple[Path, str]] = []
    if queries_change is not None:
        targets.append(queries_change)
    targets.append((skill_md_path, skill_md_text))

    for target_path, _new_text in targets:
        snapshots[target_path] = (
            target_path.read_text(encoding="utf-8")
            if target_path.is_file() else None
        )

    # 4. Commit. Roll back on first failure.
    written: list[Path] = []
    try:
        for target_path, new_text in targets:
            _atomic_write(target_path, new_text)
            written.append(target_path)
        # State cache is best-effort: failure here doesn't trigger
        # rollback because no user-facing artefact has changed (the
        # cache is just for ``skill update`` replay).
        state_cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            state_cache_path.write_text(state_cache_text, encoding="utf-8")
        except OSError as exc:  # pragma: no cover — disk full at flush
            _stderr.print(
                f"[yellow]skill init: could not write state cache "
                f"({exc}); future ``skill update`` may need ``--non-"
                f"interactive`` re-prompting.[/yellow]"
            )
    except Exception:
        for target_path in written:
            snap = snapshots.get(target_path)
            try:
                if snap is None:
                    target_path.unlink(missing_ok=True)
                else:
                    target_path.write_text(snap, encoding="utf-8")
            except OSError:
                pass
        raise


# ─── State cache (idempotency) ─────────────────────────────────────────


def _load_state_cache() -> dict[str, Any] | None:
    path = _state_cache_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ─── Wizard orchestration ─────────────────────────────────────────────


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_wizard(
    *,
    profile: str | None,
    target_skills_dir: Path | None,
    non_interactive: bool,
) -> SkillPlan:
    """Run the wizard end-to-end and return the committed plan.

    Tests call this directly with monkeypatched prompts; the Typer
    entry points wrap it with output-formatting + exit-code mapping.
    """
    payload = _load_describe_payload(profile=profile)
    profile_name = payload.get("profile") or profile or "default"
    describe_version = payload.get("version", "0.1")
    payload_hash = _payload_hash(payload)

    cache: dict[str, Any] | None = None
    if non_interactive:
        cache = _load_state_cache()
        if cache is None:
            raise SkillInitError(
                "no interview cache found; run ``bcli skill init`` "
                "interactively first."
            )
        if cache.get("describe_payload_hash") != payload_hash:
            raise SkillInitError(
                "describe payload changed since the last init "
                "(source_hash mismatch); re-run ``bcli skill init`` "
                "interactively to refresh."
            )
        interview = InterviewState(**cache["interview"])
    else:
        interview = _interview_interactively()

    queries = _load_existing_queries(profile_name)
    slash_commands = _surface_queries_for_interests(queries, interview.top_three)

    approved: tuple[ProposedQuery, ...]
    if non_interactive:
        # Replay the previously-approved queries verbatim. The operator
        # already consented during the prior interactive ``init``, so
        # re-asking would break the documented idempotency contract.
        # ``cache`` is guaranteed non-None here (set above).
        assert cache is not None
        cached_approved = cache.get("approved_new_queries", []) or []
        approved = tuple(
            ProposedQuery(name=entry["name"], body=dict(entry["body"]))
            for entry in cached_approved
        )
    else:
        proposed: list[ProposedQuery] = []
        if interview.generate_new:
            proposed = _collect_proposed_new_queries(interview, payload)
        approved = _approve_each_proposal(proposed)

    target_dir = Path(target_skills_dir) if target_skills_dir else _user_skill_dir()

    plan = SkillPlan(
        profile=profile_name,
        describe_version=describe_version,
        payload_hash=payload_hash,
        interview=interview,
        slash_commands=slash_commands,
        approved_new_queries=approved,
        generated_at=_iso_utc_now(),
        target_skills_dir=target_dir,
    )
    _commit_plan(plan)
    return plan


# ─── Typer entry points ───────────────────────────────────────────────


@app.command("init")
def init_command(
    profile: str | None = typer.Option(
        None, "--profile", help="Profile to project (defaults to active).",
    ),
    target_skills_dir: Path | None = typer.Option(
        None,
        "--target-skills-dir",
        help="Override the output skill-bundle directory (default: "
             "``~/.claude/skills/bcli-<user>/``).",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Replay a prior interview from the local cache; refuses if "
             "the describe payload changed.",
    ),
) -> None:
    """Interview the user and generate a per-user bcli skill bundle.

    Run once per profile to bootstrap. ``bcli skill update`` re-runs
    the same flow against the current describe surface.
    """
    try:
        plan = run_wizard(
            profile=profile,
            target_skills_dir=target_skills_dir,
            non_interactive=non_interactive,
        )
    except SkillInitError as exc:
        _stderr.print(f"[red]skill init: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]✓[/green] Wrote skill bundle to "
        f"{plan.target_skills_dir}/SKILL.md "
        f"({len(plan.slash_commands)} surfaced, "
        f"{len(plan.approved_new_queries)} new query/queries)."
    )


@app.command("update")
def update_command(
    profile: str | None = typer.Option(
        None, "--profile", help="Profile to refresh (defaults to active).",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Replay the cached interview without prompting.",
    ),
) -> None:
    """Re-run the wizard against the current describe surface.

    With ``--non-interactive`` the previous interview is replayed
    verbatim; if the describe surface changed (different source_hash)
    the command refuses so the operator notices and runs ``init``.
    """
    # ``update`` and ``init`` share the same implementation; the only
    # behavioural difference is the help string and the user's mental
    # model. Keeping them as separate Typer commands lets us evolve them
    # independently in v0.2.
    init_command(
        profile=profile,
        target_skills_dir=None,
        non_interactive=non_interactive,
    )
