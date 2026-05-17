"""``bcli skill install`` generates per-query + per-batch slash commands.

Decisions baked into these tests (see PR body and docs/saved-queries.md):

* ``args:`` is OPTIONAL on a saved query — if missing, the installer
  derives it from ``params:`` (required first, then with-default, both
  in YAML insertion order). Existing saved-query bundles work
  unchanged.
* ``bcli skill`` is a Typer **group** so Worker B can land
  ``bcli skill init`` (Phase 7 wizard) alongside without restructuring.
* ``manual: true`` opt-out is YAML **frontmatter** only, not a comment.
* Skill index lives at ``<target>/.claude/skills/bcli/SKILL.md`` —
  matches Claude Code's documented skill-loading convention.
* Idempotency: each generated file embeds a ``content_hash:`` of the
  rest of the body; re-runs that produce the same hash are a no-op.
  No ``generated_at`` timestamp (would invalidate the hash on every run).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.app import app

runner = CliRunner()


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Scope HOME / Path.home to tmp_path so the installer's source-of-truth
    paths (queries dir, batches dir, default target) all live under
    ``tmp_path``. The CONFIG_DIR module constants are patched so
    ``query_cmd.QUERIES_DIR`` and our new ``BATCHES_DIR`` resolve here.
    """
    config_dir = tmp_path / ".config" / "bcli"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("bcli.config._defaults.CONFIG_DIR", config_dir)
    # query_cmd binds QUERIES_DIR at module load — patch the attribute
    # directly so the installer reads from our tmp tree.
    monkeypatch.setattr(
        "bcli_cli.commands.query_cmd.QUERIES_DIR", config_dir / "queries",
    )
    monkeypatch.setattr(
        "bcli_cli.commands.skill_cmd.QUERIES_DIR", config_dir / "queries",
        raising=False,
    )
    monkeypatch.setattr(
        "bcli_cli.commands.skill_cmd.BATCHES_DIR", config_dir / "batches",
        raising=False,
    )
    yield tmp_path


@pytest.fixture
def cli_state():
    """Active profile required so the installer can resolve its sources."""
    cfg = BCConfig(
        defaults=BCDefaults(profile="finance"),
        profiles={
            "finance": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                company_id="c-1",
                disable_writes=False,
            ),
        },
    )
    state._config = cfg
    state._registry = None
    state._telemetry = None
    state.profile_name = None
    state.env_override = None
    state.company_override = None
    state.format = "table"
    state.dry_run = False
    state.quiet = True
    yield state
    state._config = None
    state._registry = None
    state._telemetry = None
    state.profile_name = None


def _write_queries(home: Path, body: str) -> Path:
    f = home / ".config" / "bcli" / "queries" / "finance.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(dedent(body).strip() + "\n", encoding="utf-8")
    return f


def _write_batch(home: Path, name: str, body: str) -> Path:
    f = home / ".config" / "bcli" / "batches" / "finance" / f"{name}.yaml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(dedent(body).strip() + "\n", encoding="utf-8")
    return f


def _invoke(*args: str):
    return runner.invoke(app, ["skill", "install", *args])


# ─── Command per saved query ────────────────────────────────────────


def test_skill_install_creates_command_per_saved_query(isolated_home, cli_state):
    _write_queries(isolated_home, """
        queries:
          utilization-by-esn:
            description: Engine utilization for an ESN
            categories: [aviation, daily-ops]
            args:
              - name: esn
                type: string
                example: "424322"
                required: true
            endpoint: util_history
            filter: "engine_serial eq '${{ args.esn }}'"

          customer-by-name:
            description: Look up a customer by display name
            categories: [finance]
            endpoint: customers
            params:
              name:
                required: true
                type: string
            filter: "displayName eq '${{ params.name }}'"
    """)
    target = isolated_home / "proj"
    target.mkdir()
    result = _invoke("--target", str(target))
    assert result.exit_code == 0, result.stdout + result.stderr

    cmds_dir = target / ".claude" / "commands"
    assert cmds_dir.is_dir()
    util = cmds_dir / "bcli-utilization-by-esn.md"
    cust = cmds_dir / "bcli-customer-by-name.md"
    assert util.is_file()
    assert cust.is_file()

    util_text = util.read_text(encoding="utf-8")
    # Frontmatter has a description.
    assert "description: Engine utilization for an ESN" in util_text
    # The body invokes ``bcli q ...`` with the args threaded positionally.
    assert "bcli q utilization-by-esn esn=$1" in util_text
    # The body references --format json (agent-friendly).
    assert "--format json" in util_text

    # customer-by-name has no explicit args list — installer derives one
    # from the params keys.
    cust_text = cust.read_text(encoding="utf-8")
    assert "bcli q customer-by-name name=$1" in cust_text


def test_skill_install_args_inferred_from_params_when_omitted(isolated_home, cli_state):
    """No ``args:`` on a query → installer derives it from ``params:`` order.

    Required params come first; optional (with ``default``) follow. Both
    in YAML insertion order so the slash command is stable.
    """
    _write_queries(isolated_home, """
        queries:
          open-invoices:
            description: Outstanding invoices
            endpoint: customerSalesInvoices
            params:
              customer-id:
                required: true
              limit:
                default: 50
              order:
                default: dueDate
    """)
    target = isolated_home / "proj"
    target.mkdir()
    _invoke("--target", str(target))
    md = (target / ".claude" / "commands" / "bcli-open-invoices.md").read_text()
    # customer-id (required) first, then limit + order (optional) — all
    # threaded as positional → key form so a /bcli-open-invoices call
    # works ergonomically.
    assert "bcli q open-invoices customer-id=$1 limit=$2 order=$3" in md


def test_skill_install_renders_argument_hint_in_frontmatter(isolated_home, cli_state):
    _write_queries(isolated_home, """
        queries:
          vendor-by-no:
            description: Vendor by number
            args:
              - {name: vendor-no, required: true, example: "V00010"}
            endpoint: vendors
    """)
    target = isolated_home / "proj"
    target.mkdir()
    _invoke("--target", str(target))
    md = (target / ".claude" / "commands" / "bcli-vendor-by-no.md").read_text()
    # Required arg shown without brackets; optional args (none here) would
    # be ``[name]``. Format: "<vendor-no>".
    assert re.search(r"argument-hint:\s*<vendor-no>", md)


# ─── Command per batch YAML ─────────────────────────────────────────


def test_skill_install_creates_command_per_batch_yaml(isolated_home, cli_state):
    _write_batch(isolated_home, "engine-360", """
        name: engine-360
        description: Full engine 360 for a given ESN
        categories: [aviation]
        params:
          esn:
            required: true
        steps:
          - action: get
            endpoint: engines
            params:
              filter: "serial eq '${{ params.esn }}'"
    """)
    target = isolated_home / "proj"
    target.mkdir()
    _invoke("--target", str(target))
    md = (target / ".claude" / "commands" / "bcli-batch-engine-360.md")
    assert md.is_file()
    text = md.read_text(encoding="utf-8")
    # Body invokes bcli batch run with --format json + --result-out.
    assert "bcli batch run" in text
    assert "engine-360" in text
    assert "--format json" in text
    assert "--result-out" in text


# ─── Skill index ────────────────────────────────────────────────────


def test_skill_install_generates_index_grouped_by_categories(
    isolated_home, cli_state,
):
    _write_queries(isolated_home, """
        queries:
          util-a:
            description: Aviation util A
            categories: [aviation]
            endpoint: a
          util-b:
            description: Aviation util B
            categories: [aviation]
            endpoint: b
          customer-by-name:
            description: Look up a customer
            categories: [finance]
            endpoint: customers
            params:
              name: {required: true}
          orphan:
            description: Uncategorized
            endpoint: orphans
    """)
    target = isolated_home / "proj"
    target.mkdir()
    _invoke("--target", str(target))

    index = target / ".claude" / "skills" / "bcli" / "SKILL.md"
    assert index.is_file()
    body = index.read_text(encoding="utf-8")
    # Each category is a section header. Headings are sorted
    # alphabetically for deterministic output; ``unsorted`` (the
    # fallback bucket for the ``orphan`` query) sorts to the end.
    assert "## aviation" in body
    assert "## finance" in body
    assert "## unsorted" in body
    # Each command listed under its category.
    assert "bcli-util-a" in body
    assert "bcli-util-b" in body
    assert "bcli-customer-by-name" in body
    assert "bcli-orphan" in body


# ─── Idempotency ─────────────────────────────────────────────────────


def test_skill_install_idempotent_no_changes(isolated_home, cli_state):
    """Second run on unchanged sources rewrites nothing.

    Verified by mtime: the file's mtime after the second run equals
    the first run's mtime.
    """
    _write_queries(isolated_home, """
        queries:
          q1:
            description: One
            categories: [a]
            endpoint: ones
    """)
    target = isolated_home / "proj"
    target.mkdir()
    _invoke("--target", str(target))
    md = target / ".claude" / "commands" / "bcli-q1.md"
    mtime_1 = md.stat().st_mtime_ns

    import time as _t
    _t.sleep(0.05)  # ensure mtime granularity differs if we did write

    _invoke("--target", str(target))
    mtime_2 = md.stat().st_mtime_ns
    assert mtime_2 == mtime_1, (
        "second run must NOT rewrite an unchanged generated file"
    )


def test_skill_install_rewrites_when_source_changes(isolated_home, cli_state):
    """When the saved-query YAML changes, the generated file regenerates."""
    _write_queries(isolated_home, """
        queries:
          q1:
            description: One
            endpoint: ones
    """)
    target = isolated_home / "proj"
    target.mkdir()
    _invoke("--target", str(target))
    md = target / ".claude" / "commands" / "bcli-q1.md"
    text_1 = md.read_text(encoding="utf-8")

    # Change the description.
    _write_queries(isolated_home, """
        queries:
          q1:
            description: Now Two
            endpoint: ones
    """)
    _invoke("--target", str(target))
    text_2 = md.read_text(encoding="utf-8")
    assert text_1 != text_2
    assert "Now Two" in text_2


# ─── Manual file preservation ───────────────────────────────────────


def test_skill_install_preserves_manual_files(isolated_home, cli_state):
    """A manually-edited slash command file is NEVER overwritten.

    Convention: YAML frontmatter ``manual: true``. Comment-style
    ``# manual: true`` is intentionally NOT honored.
    """
    _write_queries(isolated_home, """
        queries:
          custom-tool:
            description: Auto-generated description
            endpoint: x
    """)
    target = isolated_home / "proj"
    target.mkdir()
    cmds_dir = target / ".claude" / "commands"
    cmds_dir.mkdir(parents=True)
    manual = cmds_dir / "bcli-custom-tool.md"
    manual_body = dedent("""
        ---
        manual: true
        description: HAND CRAFTED
        ---

        Custom body, do not touch.
    """).strip()
    manual.write_text(manual_body, encoding="utf-8")

    _invoke("--target", str(target))

    assert manual.read_text(encoding="utf-8") == manual_body, (
        "frontmatter `manual: true` must protect the file from regeneration"
    )


# ─── Dry-run ─────────────────────────────────────────────────────────


def test_skill_install_dry_run_writes_nothing(isolated_home, cli_state):
    _write_queries(isolated_home, """
        queries:
          q1:
            description: One
            endpoint: ones
    """)
    target = isolated_home / "proj"
    target.mkdir()
    result = _invoke("--target", str(target), "--dry-run")
    assert result.exit_code == 0
    assert not (target / ".claude" / "commands" / "bcli-q1.md").exists()
    # Some announcement that this would have been generated.
    stdout = result.stdout
    assert "bcli-q1" in stdout
    assert "dry-run" in stdout.lower() or "would" in stdout.lower()


# ─── Hash idempotency primitive ─────────────────────────────────────


def test_skill_install_embedded_content_hash_is_stable(isolated_home, cli_state):
    """The provenance ``content_hash`` is computed over the file body
    *excluding* the hash line itself, so it stays stable across runs.
    A consumer can recompute it locally and verify integrity.
    """
    _write_queries(isolated_home, """
        queries:
          q1:
            description: Stable
            endpoint: ones
    """)
    target = isolated_home / "proj"
    target.mkdir()
    _invoke("--target", str(target))
    md = (target / ".claude" / "commands" / "bcli-q1.md").read_text()
    m = re.search(r"content_hash:\s*sha256:([0-9a-f]{16,64})", md)
    assert m, f"expected content_hash in generated file:\n{md}"
    declared = m.group(1)

    # Recompute over the body with the entire hash *line* removed (the
    # documented verification protocol — strip the whole line, not just
    # the value, so the digest matches the producer's pre-injection body).
    stripped = re.sub(
        r"^[ \t]*content_hash:\s*sha256:[0-9a-f]+\s*\n",
        "",
        md,
        flags=re.MULTILINE,
    )
    recomputed = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
    assert recomputed == declared, (
        f"content_hash mismatch: declared={declared} recomputed={recomputed}"
    )


# ─── Target directory ───────────────────────────────────────────────


def test_skill_install_target_dir_defaults_to_cwd_when_dot_claude_present(
    isolated_home, cli_state, monkeypatch,
):
    """If CWD has a ``.claude/`` dir, default target is CWD; otherwise
    fall back to ``~/.claude/``. Tested by running once without
    ``--target`` from a dir with .claude/ present.
    """
    _write_queries(isolated_home, """
        queries:
          q1:
            description: One
            endpoint: ones
    """)
    project = isolated_home / "proj-cwd"
    project.mkdir()
    (project / ".claude").mkdir()
    monkeypatch.chdir(project)

    result = _invoke()
    assert result.exit_code == 0, result.stdout + result.stderr
    assert (project / ".claude" / "commands" / "bcli-q1.md").is_file()


# ─── Empty / missing inputs ─────────────────────────────────────────


def test_skill_install_no_queries_no_batches_is_a_noop(isolated_home, cli_state):
    """No saved queries + no batch templates → command succeeds with a
    helpful message; no files written under .claude/commands/."""
    target = isolated_home / "proj"
    target.mkdir()
    result = _invoke("--target", str(target))
    assert result.exit_code == 0
    cmds_dir = target / ".claude" / "commands"
    # Either directory is missing entirely OR it exists empty — both fine.
    if cmds_dir.exists():
        assert not any(cmds_dir.glob("bcli-*.md"))
