"""Tests for ``bcli_cli._data_arg`` — the shared ``--data``/``-d`` parser
used by ``post``, ``patch``, and ``action``.

Before this module existed, each command had its own unguarded
``json.loads()`` call: a malformed inline literal or a bare file path
passed without ``@`` surfaced as a raw ``json.JSONDecodeError``
traceback. Every case here must raise ``typer.BadParameter`` instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from bcli_cli._data_arg import parse_data_argument


class TestValidInput:
    def test_valid_inline_json(self):
        assert parse_data_argument('{"a": 1, "b": "two"}') == {"a": 1, "b": "two"}

    def test_valid_file(self, tmp_path: Path):
        f = tmp_path / "payload.json"
        f.write_text('{"loaded": "from-file"}', encoding="utf-8")
        assert parse_data_argument(f"@{f}") == {"loaded": "from-file"}


class TestMissingFile:
    def test_missing_at_file_names_the_path(self):
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument("@/no/such/file.json")
        assert "File not found" in str(exc_info.value)
        assert "/no/such/file.json" in str(exc_info.value)


class TestMalformedInlineJson:
    def test_raises_bad_parameter_not_json_decode_error(self):
        """The core bug: a decode failure must never escape as a raw
        json.JSONDecodeError — it has to become a usage error."""
        with pytest.raises(typer.BadParameter):
            parse_data_argument("{not valid json")
        # And specifically NOT a bare JSONDecodeError bubbling past us.
        try:
            parse_data_argument("{not valid json")
        except typer.BadParameter:
            pass
        except json.JSONDecodeError:
            pytest.fail("json.JSONDecodeError leaked instead of BadParameter")

    def test_message_includes_line_col_and_reason(self):
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument("{not valid json")
        msg = str(exc_info.value)
        assert "line 1" in msg
        assert "column" in msg

    def test_message_includes_truncated_excerpt(self):
        long_garbage = "x" * 300
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument(long_garbage)
        msg = str(exc_info.value)
        # Never dump the whole payload.
        assert long_garbage not in msg
        assert "…" in msg

    def test_generic_garbage_gets_no_spurious_hint(self):
        """Plain nonsense that isn't path-like or shell-mangled should
        get the base message only — no misleading hint appended."""
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument("definitely not json")
        msg = str(exc_info.value)
        assert "@" not in msg
        assert "shell" not in msg.lower()


class TestBareFilePathTrap:
    """The exact trap from the bug report: a user passes a real file
    path to --data without the @ prefix."""

    def test_existing_file_path_suggests_at_form(self, tmp_path: Path):
        f = tmp_path / "payload.json"
        f.write_text('{"x": 1}', encoding="utf-8")
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument(str(f))
        msg = str(exc_info.value)
        assert "looks like a file path" in msg
        assert f"-d @{f}" in msg

    def test_windows_drive_path_suggests_at_form_even_if_missing(self):
        # Backslash path with a drive letter — classic Windows/PowerShell
        # shape. Doesn't exist on this (or any) filesystem, but the shape
        # alone is a strong enough signal to hint.
        windows_path = r"C:\Users\test\payload.json"
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument(windows_path)
        msg = str(exc_info.value)
        assert "looks like a file path" in msg
        assert f"-d @{windows_path}" in msg

    def test_nonexistent_but_path_shaped_string_still_hints(self):
        # Has a separator and a .json suffix but doesn't exist — weaker
        # signal than an existing file, but still worth a hint.
        path_like = "some/dir/payload.json"
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument(path_like)
        msg = str(exc_info.value)
        assert "looks like a file path" in msg

    def test_bare_word_without_json_suffix_gets_no_path_hint(self):
        # No separator, no .json suffix, not a real file — nothing here
        # actually looks like a path, so no path hint should fire.
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument("archive")
        msg = str(exc_info.value)
        assert "looks like a file path" not in msg


class TestShellManglingTrap:
    """PowerShell (and other shells) can strip the quotes out of an
    inline JSON literal before bcli ever sees it."""

    def test_no_quotes_at_all_hints_shell_mangling(self):
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument("{bladeType: HPT BLADE}")
        msg = str(exc_info.value)
        assert "shell" in msg.lower()
        assert "-d @payload.json" in msg

    def test_unquoted_keys_with_some_quotes_hints_shell_mangling(self):
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument('{bladeType: "HPT BLADE", qty: 5}')
        msg = str(exc_info.value)
        assert "shell" in msg.lower()

    def test_hint_is_a_single_sentence(self):
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument("{bladeType: HPT BLADE}")
        hint_line = str(exc_info.value).splitlines()[-1].strip()
        # One sentence: exactly one terminal period, not a lecture. (The
        # "e.g." abbreviation has its own internal periods, so strip it
        # before counting sentence-ending ". ".)
        assert hint_line.replace("e.g.", "eg").count(". ") == 0
        assert hint_line.endswith(".")


class TestMalformedFile:
    def test_malformed_at_file_names_the_file_path(self, tmp_path: Path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(typer.BadParameter) as exc_info:
            parse_data_argument(f"@{f}")
        msg = str(exc_info.value)
        assert str(f) in msg
        assert "line 1" in msg

    def test_malformed_at_file_not_a_json_decode_error(self, tmp_path: Path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json", encoding="utf-8")
        try:
            parse_data_argument(f"@{f}")
        except typer.BadParameter:
            pass
        except json.JSONDecodeError:
            pytest.fail("json.JSONDecodeError leaked instead of BadParameter")
