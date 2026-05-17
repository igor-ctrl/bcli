"""Pin the AIP §Phase 4 exit-code taxonomy values and helper behavior."""

from __future__ import annotations

from bcli.exit_codes import (
    EXIT_AUTH,
    EXIT_GENERIC_ERROR,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_POLICY,
    EXIT_REMOTE_4XX,
    EXIT_REMOTE_5XX,
    EXIT_USAGE,
    EXIT_VALIDATION,
    EXIT_CODES,
    describe_exit_code,
    exit_code_for_status,
)


def test_taxonomy_values_match_contract():
    """These integers are the public contract. Bumping them is breaking."""
    assert EXIT_OK == 0
    assert EXIT_GENERIC_ERROR == 1
    assert EXIT_USAGE == 2
    assert EXIT_AUTH == 3
    assert EXIT_NOT_FOUND == 4
    assert EXIT_VALIDATION == 5
    assert EXIT_REMOTE_4XX == 6
    assert EXIT_REMOTE_5XX == 7
    assert EXIT_POLICY == 8


def test_exit_codes_map_covers_full_taxonomy():
    """`EXIT_CODES` is the data the `bcli describe` projection consumes."""
    for code in (0, 1, 2, 3, 4, 5, 6, 7, 8):
        assert code in EXIT_CODES
        assert isinstance(EXIT_CODES[code], str) and EXIT_CODES[code]


def test_describe_exit_code_returns_label():
    assert describe_exit_code(0) == EXIT_CODES[0]
    assert describe_exit_code(8) == EXIT_CODES[8]


def test_exit_code_for_status_4xx_5xx():
    assert exit_code_for_status(400) == EXIT_REMOTE_4XX
    assert exit_code_for_status(404) == EXIT_REMOTE_4XX
    assert exit_code_for_status(429) == EXIT_REMOTE_4XX
    assert exit_code_for_status(500) == EXIT_REMOTE_5XX
    assert exit_code_for_status(503) == EXIT_REMOTE_5XX


def test_exit_code_for_status_none_or_other_returns_generic():
    assert exit_code_for_status(None) == EXIT_GENERIC_ERROR
    assert exit_code_for_status(200) == EXIT_GENERIC_ERROR
    assert exit_code_for_status(0) == EXIT_GENERIC_ERROR
