"""Dataclass round-trip + prompt-text rendering for ContextBundle."""

from __future__ import annotations

import json

from bcli.context import (
    Attachment,
    BundlePolicy,
    BundleSource,
    ContextBundle,
    HttpEvent,
    LastErrorRecord,
    ProfileSnapshot,
    RedactionRecord,
    TokenBudget,
)


def test_default_bundle_round_trips() -> None:
    bundle = ContextBundle()
    payload = bundle.to_dict()
    # to_dict() must be JSON serializable.
    text = json.dumps(payload)
    again = json.loads(text)
    assert again["schema_version"] == bundle.schema_version
    assert again["sources"] == []
    assert again["redactions"] == []
    assert again["last_error"] is None


def test_filled_bundle_to_dict_preserves_fields() -> None:
    bundle = ContextBundle(
        generated_at="2026-05-22T10:00:00+00:00",
        question="why 400?",
        budget=TokenBudget(max_tokens=8000, actual_tokens=42, truncated=False),
        policy=BundlePolicy(include_bodies=True, include_describe=True),
        sources=(
            BundleSource(kind="question", label="user-question", included_bytes=7),
        ),
        redactions=(
            RedactionRecord(
                rule_id="audit:key",
                location_path="root.headers.Authorization",
                redacted_length=64,
            ),
        ),
        profile_snapshot=ProfileSnapshot(
            name="production",
            environment="Production",
            company="Contoso",
            auth_method="client_credentials",
            disable_writes=True,
        ),
        registry_snapshot_hash="sha256:abc",
        describe_excerpt='{"version": "0.4.0"}',
        recent_http=(
            HttpEvent(
                timestamp="2026-05-22T09:59:00+00:00",
                method="GET",
                url="https://api.businesscentral.dynamics.com/.../vendors",
                status=400,
                latency_ms=120.0,
                correlation_id="corr-123",
                endpoint="vendors",
                retry_count=0,
            ),
        ),
        last_error=LastErrorRecord(
            timestamp="2026-05-22T09:59:01+00:00",
            command="get vendors --filter junk",
            error_class="ValidationError",
            exit_code=2,
            status=400,
            profile="production",
            environment="Production",
            company="Contoso",
            url="https://example/api",
            method="GET",
            correlation_id="corr-123",
            endpoint="vendors",
            hint="check OData syntax",
            bc_message="bad filter",
            traceback_excerpt="",
        ),
        attachments=(
            Attachment(
                label="batch.yaml",
                path="/tmp/batch.yaml",
                content="steps: []\n",
                original_bytes=10,
                included_bytes=10,
            ),
        ),
    )

    payload = bundle.to_dict()
    again = json.loads(json.dumps(payload))
    assert again["question"] == "why 400?"
    assert again["budget"]["max_tokens"] == 8000
    assert again["profile_snapshot"]["name"] == "production"
    assert again["last_error"]["error_class"] == "ValidationError"
    assert again["redactions"][0]["rule_id"] == "audit:key"
    assert again["attachments"][0]["label"] == "batch.yaml"


def test_to_prompt_text_includes_priority_sections() -> None:
    bundle = ContextBundle(
        question="why?",
        profile_snapshot=ProfileSnapshot(name="prod", environment="P"),
        last_error=LastErrorRecord(
            timestamp="t",
            command="bcli get x",
            error_class="ValidationError",
            exit_code=2,
            status=400,
            bc_message="bad filter",
        ),
        recent_http=(
            HttpEvent(
                timestamp="t",
                method="GET",
                url="https://example/api",
                status=400,
            ),
        ),
        redactions=(
            RedactionRecord(
                rule_id="audit:key",
                location_path="x.Authorization",
                redacted_length=64,
            ),
        ),
    )
    text = bundle.to_prompt_text()
    assert "## Question" in text
    assert "why?" in text
    assert "## Last error" in text
    assert "ValidationError" in text
    assert "## Profile" in text
    assert "prod" in text
    assert "## Recent HTTP" in text
    assert "## Redactions applied" in text
    assert "`audit:key`: 1 occurrence(s)" in text


def test_prompt_text_omits_traceback_unless_policy_allows() -> None:
    le = LastErrorRecord(
        timestamp="t",
        command="x",
        error_class="ServerError",
        exit_code=4,
        traceback_excerpt="Traceback (most recent call last):\n  ...",
    )
    bundle_no_debug = ContextBundle(last_error=le)
    text = bundle_no_debug.to_prompt_text()
    assert "Traceback" not in text

    bundle_debug = ContextBundle(
        last_error=le,
        policy=BundlePolicy(include_debug=True),
    )
    text_debug = bundle_debug.to_prompt_text()
    assert "Traceback" in text_debug


def test_truncated_bundle_advertises_truncation() -> None:
    bundle = ContextBundle(
        question="q",
        budget=TokenBudget(max_tokens=100, actual_tokens=200, truncated=True),
    )
    text = bundle.to_prompt_text()
    assert "truncated" in text.lower()


def test_empty_bundle_prompt_is_empty_safe() -> None:
    bundle = ContextBundle()
    text = bundle.to_prompt_text()
    # Should be at most a trailing newline — no exceptions, no sections.
    assert text.strip() == ""
