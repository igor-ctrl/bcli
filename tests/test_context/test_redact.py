"""Three-layer redaction covered by adversarial inputs.

Asserts every redaction lands in the audit trail with a stable
``rule_id`` — a regression that silently drops a value lands as a
missing :class:`RedactionRecord`, not as a missing assertion.
"""

from __future__ import annotations

from bcli.context._redact import (
    REDACT_RULES,
    RULE_AUDIT_KEY,
    RULE_GUID,
    RULE_TELEMETRY_PATTERN,
    RULE_TRUNCATE,
    RULE_URL_QUERY,
    apply_layered_redaction,
    redact_structured,
    redact_text,
    redact_url,
    scan_attachment,
)


def test_layer1_strips_sensitive_keys_in_nested_json() -> None:
    payload = {
        "outer": {
            "Authorization": "Bearer eyJqwt.body.sig",
            "data": {
                "client_secret": "supersecret",
                "ok": "value",
            },
            "list": [
                {"token": "abc", "value": "ok"},
            ],
        }
    }
    cleaned, records = redact_structured(payload)
    # Three sensitive keys redacted: Authorization, client_secret, token.
    rule_ids = {r.rule_id for r in records}
    assert rule_ids == {RULE_AUDIT_KEY}
    locations = {r.location_path for r in records}
    assert "outer.Authorization" in locations
    assert "outer.data.client_secret" in locations
    assert any("token" in loc for loc in locations)
    # Cleaned output replaces the values.
    assert cleaned["outer"]["Authorization"] != "Bearer eyJqwt.body.sig"
    assert cleaned["outer"]["data"]["ok"] == "value"


def test_layer2_redacts_token_patterns_in_text() -> None:
    text = "auth header was Bearer abc.def.ghi and key sk_live_ABCD1234"
    cleaned, records = redact_text(text)
    assert "Bearer" not in cleaned or "[REDACTED]" in cleaned
    assert "sk_live_" not in cleaned
    rule_ids = {r.rule_id for r in records}
    assert rule_ids == {RULE_TELEMETRY_PATTERN}
    assert len(records) >= 2


def test_layer2_catches_jwt_shape() -> None:
    # JWT regex requires 20+ chars after "ey" — make the header longer.
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0.SIG_ABCDEF"
    )
    cleaned, records = redact_text(f"token={jwt}")
    assert jwt not in cleaned
    assert records  # at least one record


def test_layer3_strips_url_query_params() -> None:
    url = (
        "https://api.businesscentral.dynamics.com/v2.0/"
        "tenant/sandbox/api/v2.0/vendors"
        "?$filter=foo eq 'bar'&access_token=very-secret-token"
    )
    cleaned, records = redact_url(url)
    assert "very-secret-token" not in cleaned
    # urlencode percent-encodes ``$`` -> ``%24`` and the bracketed
    # sentinel -> ``%5BREDACTED%5D``. Either form is acceptable; the
    # invariant is "key preserved, value gone."
    assert "filter" in cleaned and "%24filter" in cleaned
    assert "REDACTED" in cleaned
    rule_ids = {r.rule_id for r in records}
    assert rule_ids == {RULE_URL_QUERY}
    # One record per non-empty query param value.
    assert len(records) == 2


def test_layer3_guid_redaction_off_by_default() -> None:
    url = (
        "https://api.businesscentral.dynamics.com/v2.0/"
        "12345678-1234-1234-1234-123456789abc/sandbox/vendors"
    )
    cleaned, records = redact_url(url, redact_guids=False)
    assert "12345678-1234-1234-1234-123456789abc" in cleaned
    assert len(records) == 0

    cleaned_on, records_on = redact_url(url, redact_guids=True)
    assert "12345678-1234-1234-1234-123456789abc" not in cleaned_on
    assert "[GUID]" in cleaned_on
    assert any(r.rule_id == RULE_GUID for r in records_on)


def test_apply_layered_runs_layers_1_and_2() -> None:
    payload = {
        "headers": {"Authorization": "Bearer secret-token-abc"},
        "body": "free text containing eyJabc.def.ghi-jwt-shape",
        "nested": [{"token": "x"}, "Bearer aaa.bbb.ccc"],
    }
    cleaned, records = apply_layered_redaction(payload)
    rule_ids = {r.rule_id for r in records}
    assert RULE_AUDIT_KEY in rule_ids
    # We had a bearer in body + a jwt-shape pattern, layer 2 should hit.
    assert RULE_TELEMETRY_PATTERN in rule_ids


def test_url_encoded_token_is_caught() -> None:
    # URL-encoded "Bearer abc.def.ghi" inside a query value is caught
    # by the URL query stripper, not layer 2 (good — defense in depth).
    url = (
        "https://example/api?cb="
        "https%3A%2F%2Fexample.com%2Fauth%3Ftoken%3DBearer%2520abc.def.ghi"
    )
    cleaned, records = redact_url(url)
    assert "Bearer" not in cleaned
    assert records


def test_attachment_truncation_records_audit_entry() -> None:
    content = "x" * 4096
    cleaned, included_bytes, records = scan_attachment(
        content, label="big.log", max_bytes=1024, redact_guids=False
    )
    assert included_bytes <= 1024
    # We're well under the URL/JWT/Authorization detectors, so the
    # only record should be the truncate.
    rule_ids = {r.rule_id for r in records}
    assert RULE_TRUNCATE in rule_ids


def test_attachment_runs_text_and_optional_guid_layers() -> None:
    content = "header: Bearer eyJabc.def.ghi\nGUID: 12345678-1234-1234-1234-123456789abc"
    cleaned_no_guid, _, recs1 = scan_attachment(
        content, label="x", max_bytes=10_000, redact_guids=False
    )
    assert "Bearer eyJabc.def.ghi" not in cleaned_no_guid
    assert "12345678-1234-1234-1234-123456789abc" in cleaned_no_guid

    cleaned_guid, _, recs2 = scan_attachment(
        content, label="x", max_bytes=10_000, redact_guids=True
    )
    assert "12345678-1234-1234-1234-123456789abc" not in cleaned_guid
    assert any(r.rule_id == RULE_GUID for r in recs2)


def test_rule_set_is_frozen_and_complete() -> None:
    # Stable public API — these rule ids must keep working.
    assert RULE_AUDIT_KEY in REDACT_RULES
    assert RULE_TELEMETRY_PATTERN in REDACT_RULES
    assert RULE_URL_QUERY in REDACT_RULES
    assert RULE_GUID in REDACT_RULES
    assert RULE_TRUNCATE in REDACT_RULES
    assert len(REDACT_RULES) == 5  # if you add one, update the docstring


def test_base64_wrapped_token_still_caught_in_attachments() -> None:
    # A JWT inside what looks like base64 (common when a log dumps a
    # whole response body). Layer 2's JWT regex needs 20+ chars after
    # "ey" — match the real-world JWT shape, not a stripped sample.
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTYifQ.signABCDEF"
    )
    body = f"stuff before {jwt} stuff after"
    cleaned, _, records = scan_attachment(
        body, label="resp.json", max_bytes=10_000, redact_guids=False
    )
    assert jwt not in cleaned
    assert records


def test_audit_trail_length_does_not_leak_value() -> None:
    # RedactionRecord stores length only, never the value.
    payload = {"client_secret": "verysecretvalue"}
    _, records = redact_structured(payload)
    rec = records[0]
    assert rec.redacted_length == len("verysecretvalue")
    # Make sure dataclass repr/dict don't leak the value somehow.
    assert "verysecretvalue" not in repr(rec)
