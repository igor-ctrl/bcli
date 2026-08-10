"""``bcli.odata`` and ``bcli.registry`` import surfaces.

Downstream tooling (a saved-query catalog validator, for one) needs the
filter-field helpers and the ``$metadata`` importer. Re-exporting them from
their packages is what stops those consumers reaching into ``_filter_fields``
and ``_importers`` — private modules whose signatures we'd otherwise be unable
to change without breaking them silently.
"""

from __future__ import annotations


def test_odata_exports_filter_field_helpers():
    from bcli.odata import (
        extract_field_references,
        suggest_field,
        validate_filter_fields,
    )

    assert extract_field_references("number eq '1'") == ["number"]
    assert suggest_field("numbr", ["number", "postingDate"]) == ["number"]
    assert validate_filter_fields("number eq '1'", ["number"]) is None


def test_odata_all_lists_the_new_names():
    import bcli.odata as odata

    assert {
        "extract_field_references", "suggest_field", "validate_filter_fields",
    } <= set(odata.__all__)


def test_registry_exports_metadata_importer():
    from bcli.registry import import_from_metadata

    assert callable(import_from_metadata)


def test_registry_all_lists_the_metadata_importer():
    import bcli.registry as registry

    assert "import_from_metadata" in registry.__all__
