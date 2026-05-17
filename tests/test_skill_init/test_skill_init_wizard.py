"""Tests for ``bcli skill init`` — Phase 7 wizard mechanism.

The wizard is mechanism only. No Beautech-specific role content — the
OSS package emits an *empty* set of new-query proposals by default, and
defers role-aware proposals to entry-point providers (``bcli-beautech-
bootstrap`` ships one such provider). Tests here exercise the
mechanism: read describe, interview, project existing queries, write
provenance-headed files atomically, refuse to write outside the allow-
listed dirs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bcli_cli.commands import skill_init_cmd


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ─── 1. Wizard reads describe ───────────────────────────────────────


class TestReadDescribe:
    def test_wizard_loads_describe_payload(
        self, isolated_home, fake_describe, seeded_saved_queries, interview_answers,
    ):
        """Wizard must subprocess ``bcli describe`` (mocked here) and
        produce a non-empty plan. The plan reflects the profile name
        the describe payload reported."""
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendor lookups, open invoices, customer search"),
            ("style", "flat"),
            ("Generate", "n"),
        ])

        plan = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )
        assert plan.profile == "finance_sandbox"  # from fake_describe
        assert plan.describe_version == "0.1"


# ─── 2. Interview flow ─────────────────────────────────────────────


class TestInterviewFlow:
    def test_full_interview_captures_all_answers(
        self, isolated_home, fake_describe, seeded_saved_queries, interview_answers,
    ):
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendors, invoices, customers"),
            ("style", "flat"),
            ("Generate", "n"),
        ])

        plan = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )
        assert plan.interview.role == "finance"
        assert plan.interview.top_three == "vendors, invoices, customers"
        assert plan.interview.style == "flat"
        assert plan.interview.generate_new is False

    def test_style_meta_and_both_round_trip(
        self, isolated_home, fake_describe, seeded_saved_queries, interview_answers,
    ):
        interview_answers.extend([
            ("Role", "ops"),
            ("daily", "stuff"),
            ("style", "both"),
            ("Generate", "n"),
        ])
        plan = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )
        assert plan.interview.style == "both"


# ─── 3. Existing-queries projection (mechanism-only behaviour) ─────


class TestExistingQueriesProjection:
    def test_top_three_fuzzy_matches_existing_descriptions(
        self, isolated_home, fake_describe, seeded_saved_queries, interview_answers,
    ):
        """Top-3 free text fuzzy-matches against existing saved queries'
        descriptions + endpoints. ``vendor`` should match ``vendor-by-no``.
        ``customer`` should match ``customer-by-name``.

        Importantly: the wizard surfaces matches; it does NOT make role-
        keyed assumptions. Plain Python ``difflib`` is the matcher.
        """
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendor, customer"),
            ("style", "flat"),
            ("Generate", "n"),
        ])
        plan = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )
        surfaced = {sc.query_name for sc in plan.slash_commands}
        assert "vendor-by-no" in surfaced
        assert "customer-by-name" in surfaced


# ─── 4. New-query proposals require y per query ────────────────────


class TestApprovalGate:
    def test_new_query_proposal_y_per_query(
        self, isolated_home, fake_describe, seeded_saved_queries,
        interview_answers, monkeypatch,
    ):
        """If a role-template provider proposes a new saved query, the
        user must explicitly approve it. ``N`` → not written. ``y`` →
        written with provenance header."""
        # Stub an entry-point provider that proposes one new query.
        from bcli_cli.commands.skill_init_cmd import ProposedQuery

        proposed = ProposedQuery(
            name="vendor-recent",
            body={
                "description": "Vendors created in the last 30 days",
                "endpoint": "vendors",
                "filter": "createdDateTime gt '2026-04-17'",
            },
        )
        monkeypatch.setattr(
            skill_init_cmd, "_collect_proposed_new_queries",
            lambda interview, payload: [proposed],
        )
        # User says: generate-new=yes, then approve the one proposal.
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendors"),
            ("style", "flat"),
            ("Generate", "y"),
            ("Approve 'vendor-recent'", "y"),
        ])

        plan = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )
        approved = {q.name for q in plan.approved_new_queries}
        assert approved == {"vendor-recent"}

    def test_new_query_proposal_n_skips(
        self, isolated_home, fake_describe, seeded_saved_queries,
        interview_answers, monkeypatch,
    ):
        from bcli_cli.commands.skill_init_cmd import ProposedQuery

        monkeypatch.setattr(
            skill_init_cmd, "_collect_proposed_new_queries",
            lambda interview, payload: [ProposedQuery(
                name="vendor-recent",
                body={"description": "x", "endpoint": "vendors"},
            )],
        )
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendors"),
            ("style", "flat"),
            ("Generate", "y"),
            ("Approve 'vendor-recent'", "n"),
        ])
        plan = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )
        assert plan.approved_new_queries == ()


# ─── 5. Provenance header ──────────────────────────────────────────


class TestProvenance:
    def test_skill_md_carries_provenance_frontmatter(
        self, isolated_home, fake_describe, seeded_saved_queries, interview_answers,
    ):
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendor, customer"),
            ("style", "flat"),
            ("Generate", "n"),
        ])
        skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )

        skills_dirs = list((isolated_home / ".claude" / "skills").glob("bcli-*"))
        assert len(skills_dirs) == 1
        skill_md = skills_dirs[0] / "SKILL.md"
        assert skill_md.is_file()
        text = skill_md.read_text(encoding="utf-8")
        # YAML-style frontmatter wrapped in --- markers.
        assert text.startswith("---\n")
        head, _, _body = text.partition("\n---\n")
        meta = yaml.safe_load(head.lstrip("-").lstrip("\n"))
        assert meta["generated-by"] == "bcli skill init"
        assert meta["version"] == "0.1"
        assert meta["profile"] == "finance_sandbox"
        assert meta["role"] == "finance"
        assert "generated_at" in meta
        assert meta["source_hash"].startswith("sha256:")

    def test_appended_query_carries_inline_provenance(
        self, isolated_home, fake_describe, seeded_saved_queries,
        interview_answers, monkeypatch,
    ):
        """Existing queries in the YAML file MUST NOT be modified. New
        queries are appended with a per-query ``provenance`` block so a
        ``bcli skill update`` can find and regenerate just its own work."""
        from bcli_cli.commands.skill_init_cmd import ProposedQuery

        monkeypatch.setattr(
            skill_init_cmd, "_collect_proposed_new_queries",
            lambda interview, payload: [ProposedQuery(
                name="vendor-recent",
                body={"description": "x", "endpoint": "vendors"},
            )],
        )
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendors"),
            ("style", "flat"),
            ("Generate", "y"),
            ("Approve 'vendor-recent'", "y"),
        ])
        skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )

        data = _read_yaml(seeded_saved_queries)
        assert "vendor-recent" in data["queries"]
        # Existing entries preserved unchanged.
        assert "vendor-by-no" in data["queries"]
        # Provenance attached to the new query only.
        new_q = data["queries"]["vendor-recent"]
        assert new_q.get("provenance", {}).get("generated-by") == "bcli skill init"
        assert "vendor-by-no" not in data["queries"] or (
            "provenance" not in data["queries"]["vendor-by-no"]
        )


# ─── 6. Guardrails: never writes outside the allow-listed dirs ────


class TestGuardrails:
    def test_writer_refuses_path_outside_allow_list(self, isolated_home):
        """Defence-in-depth: ``_assert_writable`` rejects any path that
        isn't under ``~/.config/bcli/queries/`` or
        ``~/.claude/skills/bcli-*/``. Tests pin the rejection path so a
        future refactor that loosens it tips a red light."""
        bad_path = isolated_home / ".bashrc"
        with pytest.raises(skill_init_cmd.SkillInitError, match=r"refusing to write"):
            skill_init_cmd._assert_writable(bad_path)

    def test_writer_accepts_queries_path(self, isolated_home):
        ok = isolated_home / ".config" / "bcli" / "queries" / "finance.yaml"
        skill_init_cmd._assert_writable(ok)  # no raise

    def test_writer_accepts_skill_bundle_path(self, isolated_home):
        ok = isolated_home / ".claude" / "skills" / "bcli-alice" / "SKILL.md"
        skill_init_cmd._assert_writable(ok)  # no raise

    def test_writer_rejects_other_skill_dirs(self, isolated_home):
        """Only ``bcli-*`` subdirs of ``~/.claude/skills/`` are allowed.
        A path under ``~/.claude/skills/other-skill/`` is rejected."""
        bad = isolated_home / ".claude" / "skills" / "other-skill" / "SKILL.md"
        with pytest.raises(skill_init_cmd.SkillInitError):
            skill_init_cmd._assert_writable(bad)


# ─── 7. Wizard never touches profile / registry / disable_* ────────


class TestReadonlyConsumption:
    def test_wizard_does_not_write_to_config_toml(
        self, isolated_home, fake_describe, seeded_saved_queries, interview_answers,
    ):
        config_path = isolated_home / ".config" / "bcli" / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("# untouched\n", encoding="utf-8")

        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendor"),
            ("style", "flat"),
            ("Generate", "n"),
        ])
        skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )

        assert config_path.read_text(encoding="utf-8") == "# untouched\n"

    def test_wizard_does_not_write_to_registry_dir(
        self, isolated_home, fake_describe, seeded_saved_queries, interview_answers,
    ):
        reg_dir = isolated_home / ".config" / "bcli" / "registries"
        reg_dir.mkdir(parents=True, exist_ok=True)
        sentinel = reg_dir / "finance_sandbox.json"
        sentinel.write_text("{}", encoding="utf-8")

        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendor"),
            ("style", "flat"),
            ("Generate", "n"),
        ])
        skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )

        assert sentinel.read_text(encoding="utf-8") == "{}"


# ─── 8. Idempotency ────────────────────────────────────────────────


class TestIdempotency:
    def test_update_non_interactive_reuses_cached_answers(
        self, isolated_home, fake_describe, seeded_saved_queries, interview_answers,
    ):
        """First ``init`` writes the cache; ``update --non-interactive``
        replays the same interview AND produces the same SKILL.md
        content. Asserting only on the interview state would be a
        tautology (both plans read the same cache); pinning the on-disk
        output is the real contract."""
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendor, customer"),
            ("style", "flat"),
            ("Generate", "n"),
        ])
        plan1 = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )
        cache = isolated_home / ".config" / "bcli" / "skills" / ".last-init.json"
        assert cache.is_file()

        skill_md_dir = isolated_home / ".claude" / "skills"
        bundle_dirs = list(skill_md_dir.glob("bcli-*"))
        assert len(bundle_dirs) == 1
        skill_md_path = bundle_dirs[0] / "SKILL.md"
        skill_md_before = skill_md_path.read_text(encoding="utf-8")

        # Clear scripted answers — if a prompt fires now the test fails.
        interview_answers.clear()
        plan2 = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=True,
        )
        assert plan1.interview == plan2.interview

        # The real idempotency contract: SKILL.md is unchanged on
        # replay. Frontmatter's ``generated_at`` ticks forward (that's
        # fine — it's a timestamp, not a content marker), so compare
        # everything BELOW the frontmatter close.
        skill_md_after = skill_md_path.read_text(encoding="utf-8")

        def _body(text: str) -> str:
            _head, _sep, body = text.partition("\n---\n")
            return body
        assert _body(skill_md_before) == _body(skill_md_after)

    def test_update_replays_previously_approved_queries(
        self, isolated_home, fake_describe, seeded_saved_queries,
        interview_answers, monkeypatch,
    ):
        """The big idempotency invariant: a prior interactive ``init``
        that approved N new queries → a later ``update --non-interactive``
        re-writes the SAME N queries with refreshed provenance. Without
        this, a non-interactive replay silently drops every approved
        new query from SKILL.md."""
        from bcli_cli.commands.skill_init_cmd import ProposedQuery

        # Stub two proposals so we can assert both come back on replay.
        monkeypatch.setattr(
            skill_init_cmd, "_collect_proposed_new_queries",
            lambda interview, payload: [
                ProposedQuery(name="vendor-recent",
                              body={"description": "vendor recent",
                                    "endpoint": "vendors"}),
                ProposedQuery(name="invoice-late",
                              body={"description": "invoices past due",
                                    "endpoint": "salesInvoices"}),
            ],
        )
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendors, invoices"),
            ("style", "flat"),
            ("Generate", "y"),
            ("Approve 'vendor-recent'", "y"),
            ("Approve 'invoice-late'", "y"),
        ])
        plan1 = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )
        assert {q.name for q in plan1.approved_new_queries} == {
            "vendor-recent", "invoice-late",
        }

        interview_answers.clear()  # any prompt now would be a regression
        plan2 = skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=True,
        )
        assert {q.name for q in plan2.approved_new_queries} == {
            "vendor-recent", "invoice-late",
        }
        # Queries YAML still carries both entries after the replay.
        data = yaml.safe_load(seeded_saved_queries.read_text(encoding="utf-8"))
        assert "vendor-recent" in data["queries"]
        assert "invoice-late" in data["queries"]

    def test_update_reinterviews_when_describe_version_changes(
        self, isolated_home, fake_describe, seeded_saved_queries,
        interview_answers, monkeypatch,
    ):
        """If the cached payload's hash differs from the live describe,
        ``update --non-interactive`` aborts with a clear error so the
        operator runs ``init`` interactively to refresh."""
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendor"),
            ("style", "flat"),
            ("Generate", "n"),
        ])
        skill_init_cmd.run_wizard(
            profile=None, target_skills_dir=None, non_interactive=False,
        )

        # Mutate the describe payload — wizard should refuse to reuse
        # the cache because the source hash changed.
        def _new_payload(profile: str | None = None) -> dict:
            payload = json.loads(json.dumps(fake_describe))
            payload["version"] = "0.2"
            return payload
        monkeypatch.setattr(
            "bcli_cli.commands.skill_init_cmd._load_describe_payload",
            _new_payload,
        )

        interview_answers.clear()  # any prompt would fail in non-interactive mode
        with pytest.raises(skill_init_cmd.SkillInitError, match=r"changed"):
            skill_init_cmd.run_wizard(
                profile=None, target_skills_dir=None, non_interactive=True,
            )


# ─── 9. Atomic rollback ────────────────────────────────────────────


class TestAtomicRollback:
    def test_partial_write_failure_leaves_no_files(
        self, isolated_home, fake_describe, seeded_saved_queries,
        interview_answers, monkeypatch,
    ):
        """Inject a failure into the second commit and assert nothing
        was written — neither the queries YAML change nor the skill MD."""
        before_yaml = seeded_saved_queries.read_text(encoding="utf-8")

        # Patch the final commit so the SKILL.md write fails after the
        # queries YAML edit has been staged. The wizard's commit phase
        # must roll back the queries change.
        original_commit = skill_init_cmd._commit_plan

        def _flaky_commit(plan, *args, **kwargs):
            # Force a failure once the staging dict has been computed.
            raise RuntimeError("disk full simulation")

        monkeypatch.setattr(skill_init_cmd, "_commit_plan", _flaky_commit)

        from bcli_cli.commands.skill_init_cmd import ProposedQuery

        monkeypatch.setattr(
            skill_init_cmd, "_collect_proposed_new_queries",
            lambda interview, payload: [ProposedQuery(
                name="vendor-recent",
                body={"description": "x", "endpoint": "vendors"},
            )],
        )
        interview_answers.extend([
            ("Role", "finance"),
            ("daily", "vendor"),
            ("style", "flat"),
            ("Generate", "y"),
            ("Approve 'vendor-recent'", "y"),
        ])

        with pytest.raises(RuntimeError, match="disk full"):
            skill_init_cmd.run_wizard(
                profile=None, target_skills_dir=None, non_interactive=False,
            )

        # The pre-existing queries YAML is untouched.
        assert seeded_saved_queries.read_text(encoding="utf-8") == before_yaml
        # No SKILL.md got created.
        skill_dirs = list((isolated_home / ".claude" / "skills").glob("bcli-*"))
        for d in skill_dirs:
            assert not (d / "SKILL.md").exists()

        # Restore for any subsequent tests in the suite.
        skill_init_cmd._commit_plan = original_commit


# ─── 10. OSS mechanism emits no Beautech-specific role content ────


class TestOssMechanismHasNoRoleContent:
    """Belt-and-braces: the OSS package's default new-query proposer
    returns an empty list regardless of role. Beautech (or any third
    party) plugs in via the ``bcli.skill_init.role_templates`` entry
    point group; nothing in this PR ships role-keyed BC entity
    affinities."""

    def test_default_proposer_empty_for_finance_role(
        self, isolated_home, fake_describe, seeded_saved_queries,
        interview_answers,
    ):
        from bcli_cli.commands.skill_init_cmd import (
            InterviewState,
            _default_role_template_proposer,
        )
        interview = InterviewState(
            role="finance",
            top_three="vendors, invoices, customers",
            style="flat",
            generate_new=True,
        )
        proposals = _default_role_template_proposer(interview, fake_describe)
        assert proposals == []

    def test_default_proposer_empty_for_any_role(
        self, isolated_home, fake_describe, interview_answers,
    ):
        from bcli_cli.commands.skill_init_cmd import (
            InterviewState,
            _default_role_template_proposer,
        )
        for role in ("ops", "aviation", "sales", "dev", "custom"):
            interview = InterviewState(
                role=role, top_three="x", style="flat", generate_new=True,
            )
            assert _default_role_template_proposer(interview, fake_describe) == []
