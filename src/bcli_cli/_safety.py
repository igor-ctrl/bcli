"""CLI-side write-safety prompt for ``bcli post / patch / delete / acme attach``.

When the active profile sets ``disable_writes = true``, the CLI prints a
prominent warning and asks the user to confirm interactively before any
mutating call goes out. The user can short-circuit the prompt with
``--yes / -y`` (e.g. for scripted use), in which case the warning is
still emitted but no input is required.

The SDK (``AsyncBCClient``) does NOT enforce ``disable_writes`` —
programmatic users get unfiltered access. This is a CLI-only ergonomic
gate, on top of the actual security boundary (BC permission set).
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console

from bcli_cli._state import state

_console = Console(stderr=True)


def confirm_write_or_exit(method: str, endpoint: str, yes: bool = False) -> None:
    """If the active profile is read-only, warn + prompt before the write.

    Behaviour:

    * Profile *does not* set ``disable_writes`` → no-op.
    * Profile sets ``disable_writes = true`` and ``yes`` is True →
      print a warning to stderr but proceed (scripted use).
    * Profile sets ``disable_writes = true``, interactive TTY, ``yes``
      is False → print warning, prompt, accept only the literal string
      ``"yes"``; anything else exits 1.
    * Profile sets ``disable_writes = true``, *non-interactive* (no
      TTY), ``yes`` is False → exit 1 immediately. Scripts must opt in
      with ``--yes``.
    """
    profile = state.profile
    if not getattr(profile, "disable_writes", False):
        return

    profile_name = state.active_profile_name
    env = profile.environment
    _console.print(
        f"[yellow]⚠ Profile '{profile_name}' is configured as read-only "
        f"(disable_writes = true).[/yellow]\n"
        f"[yellow]  About to {method} {endpoint} on environment '{env}'.[/yellow]"
    )

    if yes:
        _console.print(
            "[dim]  --yes flag passed; proceeding without prompt.[/dim]"
        )
        return

    if not sys.stdin.isatty():
        _console.print(
            "[red]✗ Refusing to write: non-interactive session and "
            "--yes was not passed.[/red]"
        )
        raise typer.Exit(1)

    answer = typer.prompt(
        "Type 'yes' to proceed, anything else to cancel",
        default="",
        show_default=False,
    )
    if answer.strip().lower() != "yes":
        _console.print("[red]✗ Cancelled.[/red]")
        raise typer.Exit(1)
