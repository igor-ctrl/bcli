"""Tests for the audit-log redaction helper.

Redaction runs over every request body before it lands in the JSONL audit
file. The match is case-insensitive on key name; the value is replaced
wholesale with the sentinel ``REDACTED``. Nested dicts and lists are
walked recursively. Non-dict bodies (strings, lists of primitives) pass
through unchanged.
"""

from __future__ import annotations

from bcli.audit._redact import REDACTED, redact

DEFAULT_KEYS = ("password", "secret", "token", "key", "apiKey", "authorization")


def test_redact_replaces_top_level_key() -> None:
    body = {"username": "alice", "password": "hunter2"}
    out = redact(body, DEFAULT_KEYS)
    assert out == {"username": "alice", "password": REDACTED}


def test_redact_is_case_insensitive() -> None:
    body = {"Password": "x", "API_KEY": "y", "Token": "z"}
    out = redact(body, DEFAULT_KEYS)
    assert out["Password"] == REDACTED
    assert out["Token"] == REDACTED
    # API_KEY contains 'key' which is in defaults — should redact.
    assert out["API_KEY"] == REDACTED


def test_redact_walks_nested_dicts() -> None:
    body = {
        "outer": {
            "user": "alice",
            "credentials": {"token": "abc123"},
        },
    }
    out = redact(body, DEFAULT_KEYS)
    assert out["outer"]["user"] == "alice"
    assert out["outer"]["credentials"]["token"] == REDACTED


def test_redact_walks_lists_of_dicts() -> None:
    body = {"items": [{"name": "a", "secret": "x"}, {"name": "b"}]}
    out = redact(body, DEFAULT_KEYS)
    assert out["items"][0]["secret"] == REDACTED
    assert out["items"][0]["name"] == "a"
    assert out["items"][1] == {"name": "b"}


def test_redact_does_not_mutate_input() -> None:
    body = {"password": "hunter2"}
    redact(body, DEFAULT_KEYS)
    assert body == {"password": "hunter2"}


def test_redact_passes_through_non_dict_values() -> None:
    assert redact("just a string", DEFAULT_KEYS) == "just a string"
    assert redact([1, 2, 3], DEFAULT_KEYS) == [1, 2, 3]
    assert redact(None, DEFAULT_KEYS) is None


def test_redact_handles_empty_keys_list() -> None:
    body = {"password": "x"}
    out = redact(body, ())
    assert out == {"password": "x"}


def test_redact_custom_keys_extend_defaults() -> None:
    body = {"creditCard": "4111111111111111", "username": "alice"}
    out = redact(body, ("creditcard",))
    assert out["creditCard"] == REDACTED
    assert out["username"] == "alice"


def test_redact_partial_match_substring() -> None:
    """A key that *contains* a redact term as a substring redacts.

    Justified: 'apiKey', 'api_key', 'apiToken', 'sessionToken' should all
    redact even though they aren't exact matches for 'key' or 'token'.
    """
    body = {"sessionToken": "x", "apiSecret": "y", "noMatch": "z"}
    out = redact(body, ("token", "secret"))
    assert out["sessionToken"] == REDACTED
    assert out["apiSecret"] == REDACTED
    assert out["noMatch"] == "z"
