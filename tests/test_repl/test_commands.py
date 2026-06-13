"""Slash-command parsing."""

from __future__ import annotations

from bcli_cli.repl._commands import COMMANDS, help_text, parse_slash


def test_plain_message_is_not_a_command() -> None:
    assert parse_slash("how many vendors?") is None
    assert parse_slash("  what about /tmp paths  ") is None


def test_bare_slash_is_help() -> None:
    cmd = parse_slash("/")
    assert cmd is not None and cmd.name == "help"


def test_known_commands_parse() -> None:
    for name in COMMANDS:
        cmd = parse_slash(f"/{name}")
        assert cmd is not None
        assert cmd.name == name


def test_command_with_argument() -> None:
    cmd = parse_slash("/model anthropic:claude-opus-4-1")
    assert cmd is not None
    assert cmd.name == "model"
    assert cmd.arg == "anthropic:claude-opus-4-1"


def test_aliases() -> None:
    assert parse_slash("/quit").name == "exit"
    assert parse_slash("/q").name == "exit"
    assert parse_slash("/?").name == "help"


def test_unknown_command_is_flagged() -> None:
    cmd = parse_slash("/frobnicate now")
    assert cmd is not None
    assert cmd.name == "__unknown__"
    assert cmd.arg == "/frobnicate now"


def test_help_text_lists_all_commands() -> None:
    text = help_text()
    for name in COMMANDS:
        assert f"/{name}" in text
