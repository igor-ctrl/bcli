"""Tests for OData v4 single-quote escaping."""

from __future__ import annotations

from bcli.odata import escape_odata_string


def test_plain_string_unchanged():
    assert escape_odata_string("Acme") == "Acme"


def test_empty_string_unchanged():
    assert escape_odata_string("") == ""


def test_single_quote_doubled():
    assert escape_odata_string("O'Brien") == "O''Brien"


def test_multiple_single_quotes():
    assert escape_odata_string("'a'b'") == "''a''b''"


def test_injection_attempt_neutralised():
    """Mimics the saved-query injection example from the security review."""
    raw = "193208' or 1 eq 1--"
    escaped = escape_odata_string(raw)
    assert escaped == "193208'' or 1 eq 1--"
    # Quote count is even after escaping, so the literal cannot terminate early.
    assert escaped.count("'") % 2 == 0


def test_does_not_touch_double_quotes_or_backslashes():
    # OData v4 strings are single-quoted; double quotes and backslashes are
    # literal characters and must not be transformed.
    assert escape_odata_string('say "hi"') == 'say "hi"'
    assert escape_odata_string("a\\b") == "a\\b"
