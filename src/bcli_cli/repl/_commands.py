"""Slash-command parsing for the chat REPL.

Pure parsing + a small command table, kept separate from the Textual app
so the dispatch logic is unit-testable without a running UI. The app
calls :func:`parse_slash` on each submitted line; a non-``None`` result
is a command to handle, ``None`` means "send to the agent as a message".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    """A parsed slash command: ``name`` + the rest of the line as ``arg``."""

    name: str
    arg: str = ""


# name → one-line help, in display order.
COMMANDS: dict[str, str] = {
    "model": "Switch the model for this session (e.g. /model anthropic:claude-opus-4-1)",
    "profile": "Switch the bcli profile (re-resolves env, company, registry)",
    "company": "Set the default company alias for tool calls",
    "plan": "Toggle plan mode (writes become draft_batch proposals)",
    "yes": "Approve the pending write (same as the dialog's Approve)",
    "context": "Show the resolved profile / env / plan-mode context",
    "clear": "Clear the chat transcript and start a fresh turn history",
    "help": "List the slash commands",
    "exit": "Leave the chat (also: /quit, Ctrl+C)",
}

# Aliases that map onto a canonical command name.
_ALIASES: dict[str, str] = {
    "quit": "exit",
    "q": "exit",
    "?": "help",
}


def parse_slash(line: str) -> SlashCommand | None:
    """Parse a submitted line. Returns a :class:`SlashCommand` or ``None``.

    ``None`` means the line is an ordinary chat message. A line that
    starts with ``/`` but names an unknown command parses to
    ``SlashCommand("__unknown__", original)`` so the app can show a hint
    rather than silently sending a stray ``/typo`` to the model.
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    body = stripped[1:].strip()
    if not body:
        return SlashCommand("help")
    head, _, rest = body.partition(" ")
    name = head.lower()
    name = _ALIASES.get(name, name)
    if name not in COMMANDS:
        return SlashCommand("__unknown__", stripped)
    return SlashCommand(name, rest.strip())


def help_text() -> str:
    """Markdown help block listing the commands."""
    lines = ["**Slash commands**", ""]
    for name, desc in COMMANDS.items():
        lines.append(f"- `/{name}` — {desc}")
    return "\n".join(lines)


__all__ = ["COMMANDS", "SlashCommand", "help_text", "parse_slash"]
