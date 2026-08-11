"""Shared ``--data``/``-d`` parsing for ``post``, ``patch``, and ``action``.

All three verbs accept a request body as either a literal JSON string or
an ``@filename`` reference. Both had bare ``json.loads()`` calls with
nothing catching a decode failure, so a mangled inline literal (a shell
stripping quotes is the common case) or a bare file path passed without
the ``@`` prefix surfaced as a raw ``json.JSONDecodeError`` traceback
instead of a usage error naming the fix.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import typer

_EXCERPT_LIMIT = 80

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNQUOTED_KEY_RE = re.compile(r'[{,]\s*[A-Za-z_][\w-]*\s*:')


def _excerpt(value: str, limit: int = _EXCERPT_LIMIT) -> str:
    """Truncate ``value`` for safe inclusion in an error message."""
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _looks_like_path(data: str) -> bool:
    """True if ``data`` looks like a filesystem path rather than JSON.

    Only called after ``json.loads`` has already failed, so this doesn't
    need to be precise — just catch the shapes that mean "this needed an
    ``@`` prefix": a real file on disk (the strongest signal), a bare
    Windows drive path, or a path with a separator ending in ``.json``.
    """
    if data.startswith("{") or data.startswith("["):
        return False
    if Path(data).is_file():
        return True
    if _WINDOWS_DRIVE_RE.match(data):
        return True
    if ("/" in data or "\\" in data) and data.lower().endswith(".json"):
        return True
    return False


def _looks_shell_mangled(data: str) -> bool:
    """True if ``data`` looks like a shell stripped the quotes out of an
    inline JSON literal — e.g. PowerShell turning
    ``{"bladeType": "HPT BLADE"}`` into ``{bladeType: HPT BLADE}``.
    """
    if not data.startswith("{"):
        return False
    if '"' not in data:
        return True
    return bool(_UNQUOTED_KEY_RE.search(data))


def _mangling_hint(data: str) -> str | None:
    if _looks_like_path(data):
        return (
            f"'{data}' looks like a file path — pass '-d @{data}' "
            "to load it from a file."
        )
    if _looks_shell_mangled(data):
        return (
            "Some shells (e.g. PowerShell) strip quotes from inline JSON — "
            "pass the payload as a file instead: -d @payload.json."
        )
    return None


def parse_data_argument(data: str) -> dict:
    """Parse a ``--data``/``-d`` argument: a JSON literal or ``@filename``.

    Raises ``typer.BadParameter`` — never a raw ``json.JSONDecodeError`` —
    with a message that names the fix: a missing ``@`` prefix, likely
    shell quote-stripping, or (for a malformed file) the file's path.
    """
    if data.startswith("@"):
        path = Path(data[1:])
        if not path.is_file():
            raise typer.BadParameter(f"File not found: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise typer.BadParameter(
                f"{path} is not valid JSON: {e.msg} "
                f"(line {e.lineno}, column {e.colno})."
            ) from e

    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        message = (
            f"--data is not valid JSON: {e.msg} "
            f"(line {e.lineno}, column {e.colno}). Received: {_excerpt(data)!r}"
        )
        hint = _mangling_hint(data)
        if hint:
            message += "\n  " + hint
        raise typer.BadParameter(message) from e


__all__ = ["parse_data_argument"]
