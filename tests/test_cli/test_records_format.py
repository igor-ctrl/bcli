"""Tests for the records (vertical) formatter and auto-fallback."""

from __future__ import annotations


from bcli_cli.output._formatters import (
    _format_records,
    _should_auto_records,
    format_output,
)


def _wide_record() -> dict:
    """30-column engine record — the canonical "too wide for table" case."""
    return {
        "systemId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "engineSerialNumber": "100002",
        "engineType": "CF34-8C",
        "engineModel": "CF34-8C5",
        "thrustRating": "8C5",
        "initialDate": "2024-01-01",
        "lastOperationDate": "2025-01-01",
        "initialEngineTsn": "10000.00",
        "intialEngineCsn": "8000",
        "fc": "100",
        "fh": "200.00",
        "lastInvoiceDate": "2026-01-31",
        "currentTsn": "10200.00",
        "currentCsn": "8100",
        "limiter": "9000",
        "tspr": "10200.00",
        "cspr": "8100",
        "tslsv": "0",
        "cslsv": "0",
        "aprCycles": "10",
        "qec": "Neutral",
        "ittMargin": "30",
        "ittMarginNotes": "Example ECM note",
        "serviceable": "Yes",
        "notes": "Example note",
        "lessee": "",
        "operator": "",
        "engineStatus": "Available",
        "location": "Example Location",
        "systemModifiedAt": "2026-01-01T00:00:00.000Z",
    }


class TestAutoRecords:
    def test_single_wide_record_triggers_auto_records(self):
        assert _should_auto_records([_wide_record()]) is True

    def test_two_wide_records_still_triggers(self):
        assert _should_auto_records([_wide_record(), _wide_record()]) is True

    def test_three_records_does_not_trigger(self):
        # Past 2 records, vertical view starts hurting more than helping.
        assert _should_auto_records([_wide_record()] * 3) is False

    def test_few_columns_does_not_trigger(self):
        narrow = {"id": "1", "name": "Acme", "status": "open"}
        assert _should_auto_records([narrow]) is False

    def test_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("BCLI_NO_AUTO_RECORDS", "1")
        assert _should_auto_records([_wide_record()]) is False

    def test_format_output_promotes_table_to_records_for_wide_single(self, capsys):
        format_output([_wide_record()], fmt="table")
        out = capsys.readouterr().out
        assert "engineSerialNumber : 100002" in out
        assert "engineType         : CF34-8C" in out

    def test_format_output_promotes_markdown_to_records_for_wide_single(self, capsys):
        format_output([_wide_record()], fmt="markdown")
        out = capsys.readouterr().out
        # Markdown table never appears — the auto-fallback fires first.
        assert "| systemId" not in out
        assert "engineSerialNumber : 100002" in out

    def test_explicit_records_format_works(self, capsys):
        format_output([_wide_record()], fmt="records")
        out = capsys.readouterr().out
        assert "engineSerialNumber : 100002" in out

    def test_records_alias_r_works(self, capsys):
        format_output([{"a": "1", "b": "2"}], fmt="r")
        out = capsys.readouterr().out
        assert "a" in out and "1" in out

    def test_csv_format_is_not_promoted(self, capsys):
        # CSV is for pipelines; the auto-fallback would corrupt that.
        format_output([_wide_record()], fmt="csv")
        out = capsys.readouterr().out
        # CSV header line ends with a comma-separated set, not "name : value".
        assert "engineSerialNumber" in out
        assert "engineSerialNumber : 100002" not in out

    def test_explicit_format_disables_auto_promote(self, capsys):
        """`-f markdown` is a contract; honor it even on a wide single row."""
        format_output([_wide_record()], fmt="markdown", auto_format=False)
        out = capsys.readouterr().out
        # Markdown header pipe present, vertical view absent.
        assert "| systemId" in out
        assert "engineSerialNumber : 100002" not in out


class TestRecordsRendering:
    def test_multi_record_output_has_separators(self, capsys):
        _format_records([{"a": "1"}, {"a": "2"}])
        out = capsys.readouterr().out
        assert "record 1" in out
        assert "record 2" in out

    def test_single_record_omits_header(self, capsys):
        _format_records([{"a": "1"}])
        out = capsys.readouterr().out
        assert "record 1" not in out
        assert "  a" in out

    def test_multiline_cell_indents_continuation(self, capsys):
        _format_records([{"notes": "line one\nline two"}])
        out = capsys.readouterr().out
        lines = out.splitlines()
        # First line carries "notes : line one"; continuation indents past the colon.
        assert any("notes" in line and "line one" in line for line in lines)
        assert any(line.lstrip().startswith("line two") for line in lines)

    def test_drops_odata_fields(self, capsys):
        _format_records([{"id": "1", "@odata.etag": "abc"}])
        out = capsys.readouterr().out
        assert "@odata" not in out
        assert "id" in out

    def test_empty_records_is_noop(self, capsys):
        _format_records([])
        assert capsys.readouterr().out == ""
