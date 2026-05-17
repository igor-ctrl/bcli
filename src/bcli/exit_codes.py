"""Canonical bcli exit codes — AIP §Phase 4 taxonomy, seeded in Phase 2.

Phase 2 needs ``EXIT_REMOTE_4XX`` and ``EXIT_REMOTE_5XX`` for the result
envelope's ``exit_code`` field on failure. Phase 4 (Worker A) will wire
the rest into the CLI so ``bcli`` exits with the documented status.

Codes match the contract doc:

* ``0``   — success
* ``1``   — uncategorised failure (default for exceptions we can't map)
* ``2``   — usage error (wrong flag combination, invalid argument)
* ``3``   — auth failure
* ``4``   — not found
* ``5``   — input validation (client-side)
* ``6``   — remote 4xx
* ``7``   — remote 5xx
* ``8``   — policy violation (e.g. ``disable_writes`` triggered without ``--yes``)

Importing the constants keeps test assertions and CLI plumbing in lock-step.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_GENERIC_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_VALIDATION = 5
EXIT_REMOTE_4XX = 6
EXIT_REMOTE_5XX = 7
EXIT_POLICY = 8


def exit_code_for_status(status_code: int | None) -> int:
    """Map an HTTP status to a CLI exit code.

    Falls back to ``EXIT_GENERIC_ERROR`` when ``status_code`` is ``None``
    or doesn't fall in the 4xx/5xx range.
    """
    if status_code is None:
        return EXIT_GENERIC_ERROR
    if 400 <= status_code < 500:
        return EXIT_REMOTE_4XX
    if 500 <= status_code < 600:
        return EXIT_REMOTE_5XX
    return EXIT_GENERIC_ERROR
