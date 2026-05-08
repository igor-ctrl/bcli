"""Self-rescue diagnostics for ``bcli doctor``.

A team member with a broken setup should be able to run one command and see
exactly which check failed plus a one-line hint for fixing it. The check
primitives live here; the user-facing command lives in
``bcli_cli.commands.doctor_cmd``.

Each check is independent, returns a :class:`CheckResult`, and never raises.
A failing check produces a ``fail`` result with a hint, not a traceback —
the whole point is that this command runs cleanly even when other parts of
the install are broken.
"""

from bcli.diagnostics._checks import (
    CheckContext,
    CheckResult,
    CheckStatus,
    run_all_checks,
)

__all__ = [
    "CheckContext",
    "CheckResult",
    "CheckStatus",
    "run_all_checks",
]
