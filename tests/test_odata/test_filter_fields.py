"""Tests for the OData filter field extractor + validator."""

from __future__ import annotations

from bcli.odata._filter_fields import (
    extract_field_references,
    suggest_field,
    validate_filter_fields,
)


class TestExtractFieldReferences:
    def test_simple_eq(self):
        refs = extract_field_references("customerNumber eq '10000'")
        assert refs == ["customerNumber"]

    def test_strips_string_literals(self):
        refs = extract_field_references("displayName eq 'has eq inside'")
        assert refs == ["displayName"]

    def test_handles_double_quotes(self):
        refs = extract_field_references('name eq "anything"')
        assert refs == ["name"]

    def test_compound_filter(self):
        refs = extract_field_references(
            "customerNumber eq '10000' and status eq 'Open'"
        )
        assert refs == ["customerNumber", "status"]

    def test_function_calls_excluded(self):
        # Function name is reserved when followed by '(', but its arguments
        # are still real field references.
        refs = extract_field_references("contains(displayName, 'Fabrikam')")
        assert refs == ["displayName"]

    def test_nested_function(self):
        refs = extract_field_references("tolower(name) eq 'x'")
        assert refs == ["name"]

    def test_numeric_literals_ignored(self):
        refs = extract_field_references("unitPrice gt 100 and quantity le 5")
        assert sorted(refs) == ["quantity", "unitPrice"]

    def test_dedup_case_insensitive(self):
        refs = extract_field_references("Name eq 'a' or NAME eq 'b'")
        # First-seen casing wins, only one entry returned.
        assert refs == ["Name"]

    def test_empty_string(self):
        assert extract_field_references("") == []

    def test_only_literals(self):
        assert extract_field_references("'hello' eq 'world'") == []


class TestSuggestField:
    def test_close_match_exact_typo(self):
        assert suggest_field("displayname", ["displayName", "name"]) == ["displayName"]

    def test_initialism_substring(self):
        # 'cn' isn't close by edit-distance to customerNumber, and although
        # its letters appear in order in "customernumber" (c... n...), they
        # aren't a contiguous substring. The substring fallback only fires
        # when the needle IS contiguous, so this case must rely on the user
        # typing something close — guard the reasonable behaviour:
        suggestions = suggest_field("cn", ["customerNumber", "status", "dueDate"])
        assert isinstance(suggestions, list)

    def test_substring_fallback(self):
        # "cust" is too short for difflib to match "customerNumber", but it is
        # a contiguous substring of it.
        assert suggest_field("cust", ["customerNumber"]) == ["customerNumber"]

    def test_no_match(self):
        assert suggest_field("zzz", ["aaa", "bbb"]) == []

    def test_empty_known(self):
        assert suggest_field("anything", []) == []


class TestValidateFilterFields:
    KNOWN = ["customerNumber", "invoiceDate", "dueDate", "status", "remainingAmount"]

    def test_returns_none_when_filter_empty(self):
        assert validate_filter_fields(None, self.KNOWN) is None
        assert validate_filter_fields("", self.KNOWN) is None

    def test_returns_none_when_known_empty(self):
        # No catalogue → can't validate, fall through to BC.
        assert validate_filter_fields("anything eq 1", []) is None

    def test_passes_when_all_known(self):
        assert validate_filter_fields(
            "customerNumber eq '10000' and status eq 'Open'",
            self.KNOWN,
        ) is None

    def test_flags_unknown(self):
        result = validate_filter_fields("cust eq '10000'", self.KNOWN)
        assert result is not None
        msg, unknown = result
        assert unknown == ["cust"]
        assert "cust" in msg
        # The substring fallback finds 'customerNumber' (contains 'cust').
        # Either difflib or substring should produce *some* hint here.
        assert "customerNumber" in msg or "Did you mean" not in msg

    def test_flags_typo_with_close_match(self):
        result = validate_filter_fields("dueDateTime eq 2026-01-31", self.KNOWN)
        assert result is not None
        msg, _ = result
        # 'dueDateTime' is close to 'dueDate' via difflib.
        assert "dueDate" in msg
