"""``bcli_cli.repl`` — the interactive agent chat front-end.

The renderer half of the engine/renderer split: :mod:`bcli.agent`
(the SDK engine) emits :class:`~bcli.agent.AgentEvent` records, and this
package consumes them. Bare ``bcli`` on a TTY lazy-imports
:func:`launch_repl` here so the agent stack never loads for ordinary
subcommands.

Nothing in :mod:`bcli.agent` imports this package — the dependency only
ever points CLI → SDK.
"""

from __future__ import annotations


def launch_repl(*, profile: str | None = None) -> int:
    """Launch the Textual chat REPL. Returns a process exit code.

    Imported lazily by the bare-``bcli`` branch so Textual + the agent
    engine are only loaded when a human actually opens the chat.
    """
    from bcli_cli.repl._app import run_repl

    return run_repl(profile=profile)


__all__ = ["launch_repl"]
