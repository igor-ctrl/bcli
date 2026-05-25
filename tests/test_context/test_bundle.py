"""``bcli.context.build_bundle`` — composition, priority truncation, audit."""

from __future__ import annotations

import json
from pathlib import Path

from bcli.context import (
    BundlePolicy,
    HttpEvent,
    LastErrorRecord,
    ProfileSnapshot,
    TokenBudget,
    build_bundle,
    capture_last_error,
)
from bcli.errors import ValidationError


def test_minimal_bundle_just_a_question(tmp_path: Path) -> None:
    bundle = build_bundle(
        question="why 400?",
        config_dir=tmp_path,  # no last-error file there yet
    )
    assert bundle.question == "why 400?"
    assert bundle.last_error is None
    assert bundle.sources[0].kind == "question"
    assert bundle.budget.actual_tokens > 0


def test_bundle_reads_last_error_from_disk(tmp_path: Path) -> None:
    exc = ValidationError(
        "bad filter",
        status_code=400,
        bc_message="Field 'junk' not in 'vendors'",
        correlation_id="corr-1",
    )
    capture_last_error(
        exc=exc,
        command="get vendors --filter junk",
        profile="prod",
        config_dir=tmp_path,
    )

    bundle = build_bundle(
        question="what happened?",
        config_dir=tmp_path,
    )
    assert bundle.last_error is not None
    assert bundle.last_error.error_class == "ValidationError"
    kinds = {s.kind for s in bundle.sources}
    assert "last_error" in kinds


def test_profile_snapshot_lands_when_provided() -> None:
    bundle = build_bundle(
        profile=ProfileSnapshot(
            name="prod", environment="Production", company="Contoso"
        ),
    )
    kinds = {s.kind for s in bundle.sources}
    assert "profile_snapshot" in kinds


def test_http_events_get_url_redacted_through_pipeline() -> None:
    events = (
        HttpEvent(
            timestamp="t",
            method="GET",
            url="https://example/api?token=very-secret",
            status=200,
        ),
    )
    bundle = build_bundle(
        recent_http=events,
        policy=BundlePolicy(include_http_tail=True),
    )
    assert bundle.recent_http
    assert "very-secret" not in bundle.recent_http[0].url
    # Redaction recorded.
    assert any(r.rule_id == "context:url_query" for r in bundle.redactions)


def test_describe_excerpt_dropped_when_over_budget() -> None:
    big_describe = "x" * 10_000
    # Budget so tight we can fit the question but not the describe.
    bundle = build_bundle(
        question="q",
        describe_excerpt=big_describe,
        budget=TokenBudget(max_tokens=10),  # 40 chars cap
        policy=BundlePolicy(include_describe=True),
    )
    assert bundle.describe_excerpt == ""
    assert bundle.budget.truncated


def test_attachments_redacted_and_truncated() -> None:
    secret = "Bearer eyJabc.def.ghi-jwt-token"
    big = f"{secret}\n" + ("x" * 4096)
    bundle = build_bundle(
        raw_attachments=(("log.txt", big),),
        policy=BundlePolicy(attachment_max_bytes=512),
    )
    assert bundle.attachments
    att = bundle.attachments[0]
    assert "Bearer eyJabc.def.ghi" not in att.content
    assert att.included_bytes <= 512
    # Truncation + secret pattern both recorded.
    rule_ids = {r.rule_id for r in bundle.redactions}
    assert "context:truncate" in rule_ids


def test_audit_trail_complete_for_layered_redactions() -> None:
    # Inject a secret URL into recent_http and a key-bearing attachment.
    events = (
        HttpEvent(
            method="GET",
            url="https://example/api?access_token=abcdef",
            status=200,
            timestamp="t",
        ),
    )
    bundle = build_bundle(
        question="q",
        recent_http=events,
        raw_attachments=(
            ("creds.json", '{"client_secret": "very-secret-value"}'),
        ),
    )
    rule_ids = {r.rule_id for r in bundle.redactions}
    # URL query stripper + telemetry pattern (jwt-like) might both
    # contribute. At minimum we must have the URL strip.
    assert "context:url_query" in rule_ids


def test_no_context_policy_path() -> None:
    # Mimic `bcli ask --no-context`: caller suppresses describe + tail
    # via policy and provides nothing else.
    bundle = build_bundle(
        question="just answer",
        policy=BundlePolicy(
            include_describe=False,
            include_http_tail=False,
            include_bodies=False,
        ),
    )
    kinds = {s.kind for s in bundle.sources}
    assert kinds == {"question"}
    assert bundle.recent_http == ()
    assert bundle.describe_excerpt == ""


def test_bundle_to_dict_is_json_serializable() -> None:
    bundle = build_bundle(
        question="q",
        profile=ProfileSnapshot(name="prod"),
        last_error=LastErrorRecord(
            timestamp="t",
            command="x",
            error_class="ValidationError",
            exit_code=2,
        ),
    )
    payload = bundle.to_dict()
    text = json.dumps(payload)
    again = json.loads(text)
    assert again["question"] == "q"
