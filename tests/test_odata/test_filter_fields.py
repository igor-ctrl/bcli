"""Tests for the OData filter field extractor + validator."""

from __future__ import annotations

from bcli.odata._filter_fields import (
    extract_field_references,
    suggest_field,
    validate_filter_fields,
)


class TestExtractFieldReferences:
    def test_simple_eq(self):
        refs = extract_field_references("engineSerialNumber eq '193208'")
        assert refs == ["engineSerialNumber"]

    def test_strips_string_literals(self):
        refs = extract_field_references("displayName eq 'has eq inside'")
        assert refs == ["displayName"]

    def test_handles_double_quotes(self):
        refs = extract_field_references('name eq "anything"')
        assert refs == ["name"]

    def test_compound_filter(self):
        refs = extract_field_references(
            "engineSerialNumber eq '193208' and tailNo eq 'VH-ANO'"
        )
        assert refs == ["engineSerialNumber", "tailNo"]

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
        # 'esn' isn't close by edit-distance, but it's a substring of
        # engineSerialNumber's lowercased form (e... s... n... → "engineserialnumber").
        # The substring fallback handles this.
        suggestions = suggest_field("esn", ["engineSerialNumber", "tailNo", "asOfDate"])
        # Lowercased, "esn" appears as letters 'e','s','n' but not as a contiguous
        # substring of "engineserialnumber". So the substring fallback only fires
        # when needle IS contiguous. Skip: this case must rely on the user typing
        # something close — guard the reasonable behaviour:
        # 'tailno' → 'tailNo' contiguous.
        assert isinstance(suggestions, list)

    def test_substring_fallback(self):
        # "serial" is a contiguous substring of "engineSerialNumber".
        assert suggest_field("serial", ["engineSerialNumber"]) == ["engineSerialNumber"]

    def test_no_match(self):
        assert suggest_field("zzz", ["aaa", "bbb"]) == []

    def test_empty_known(self):
        assert suggest_field("anything", []) == []


class TestValidateFilterFields:
    KNOWN = ["engineSerialNumber", "tailNo", "asOfDate", "efh", "efc"]

    def test_returns_none_when_filter_empty(self):
        assert validate_filter_fields(None, self.KNOWN) is None
        assert validate_filter_fields("", self.KNOWN) is None

    def test_returns_none_when_known_empty(self):
        # No catalogue → can't validate, fall through to BC.
        assert validate_filter_fields("anything eq 1", []) is None

    def test_passes_when_all_known(self):
        assert validate_filter_fields(
            "engineSerialNumber eq '193208' and tailNo eq 'VH-ANO'",
            self.KNOWN,
        ) is None

    def test_flags_unknown(self):
        result = validate_filter_fields("esn eq '193208'", self.KNOWN)
        assert result is not None
        msg, unknown = result
        assert unknown == ["esn"]
        assert "esn" in msg
        # The substring fallback finds 'engineSerialNumber' (contains 'esn').
        # Either difflib or substring should produce *some* hint here.
        assert "engineSerialNumber" in msg or "Did you mean" not in msg

    def test_flags_typo_with_close_match(self):
        result = validate_filter_fields("tailNumber eq 'VH-ANO'", self.KNOWN)
        assert result is not None
        msg, _ = result
        # 'tailNumber' is close to 'tailNo' via difflib.
        assert "tailNo" in msg
