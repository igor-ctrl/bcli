"""Subprocess wrapper around ``bcli ... --format json``.

The MCP server's tools all delegate to the bcli CLI. This module owns the
process boundary: build the argv, set the env (so ``BCLI_PROFILE`` / a
per-call profile override are honoured), capture stdout + stderr, parse the
JSON response, and surface a clean ``ToolError`` on non-zero exits with
Rich markup stripped from the error message.

We deliberately do NOT import ``bcli`` Python modules here. Subprocess
delegation is the design — see ``docs/mcp-server.md``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

# Imported lazily so the runner module is importable in environments
# that don't have the optional ``mcp`` package installed (tests).
try:
    from mcp.server.fastmcp.exceptions import ToolError as _ToolError
except ImportError:  # pragma: no cover — optional dep missing in test envs
    _ToolError = RuntimeError  # type: ignore[assignment,misc]


# Strip Rich markup like [red]…[/red], [bold]…[/bold], [dim]…[/dim].
# Rich tags are non-nested in bcli's CLI output — a single regex sweep is
# enough. We don't try to render them, just remove them so MCP error
# messages read cleanly to the model.
_RICH_MARKUP = re.compile(r"\[/?[a-zA-Z][a-zA-Z0-9 _#=,\-]*\]")


def _strip_rich(text: str) -> str:
    return _RICH_MARKUP.sub("", text).strip()


def run_bcli_json(
    *args: str,
    profile: str | None = None,
    timeout: float = 120.0,
) -> Any:
    """Run ``bcli <args> --format json`` and return parsed JSON.

    ``profile`` overrides ``BCLI_PROFILE`` for this call only (the env-var
    inherited by the MCP server still acts as the default). Other env vars
    pass through unchanged.

    Raises ``ToolError`` on non-zero exit, malformed JSON, or timeout. The
    error message has Rich markup stripped so the agent sees plain text.
    """
    argv = ["bcli"]
    if profile:
        argv.extend(["--profile", profile])
    argv.extend(args)
    argv.extend(["--format", "json"])

    env = os.environ.copy()
    if profile:
        env["BCLI_PROFILE"] = profile

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise _ToolError(
            "bcli executable not found on PATH. Install with "
            "'pip install bc-cli[cli]' or 'uv tool install bc-cli'."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise _ToolError(
            f"bcli {' '.join(args)} timed out after {timeout}s"
        ) from exc

    if proc.returncode != 0:
        message = _strip_rich(proc.stderr or proc.stdout or "(no output)")
        raise _ToolError(
            f"bcli {' '.join(args)} exited {proc.returncode}: {message}"
        )

    if not proc.stdout.strip():
        # Some CLI commands print to stderr only when there's no data;
        # surface that as an empty list rather than an error.
        return []

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise _ToolError(
            f"bcli {' '.join(args)} produced non-JSON output: {exc}"
        ) from exc


def run_bcli_side_effect(
    *args: str,
    profile: str | None = None,
    timeout: float = 120.0,
) -> None:
    """Run ``bcli <args>`` for its side effect; ignore stdout content.

    Some bcli subcommands (e.g. ``endpoint fields``) emit human-readable
    text on stdout but persist their useful work to the local registry as
    a side effect. We don't want that text — we want the cache write.

    Raises ``ToolError`` on non-zero exit (with Rich markup stripped) or
    timeout. Otherwise silent on success.
    """
    argv = ["bcli"]
    if profile:
        argv.extend(["--profile", profile])
    argv.extend(args)

    env = os.environ.copy()
    if profile:
        env["BCLI_PROFILE"] = profile

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise _ToolError(
            "bcli executable not found on PATH. Install with "
            "'pip install bc-cli[cli]' or 'uv tool install bc-cli'."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise _ToolError(
            f"bcli {' '.join(args)} timed out after {timeout}s"
        ) from exc

    if proc.returncode != 0:
        message = _strip_rich(proc.stderr or proc.stdout or "(no output)")
        raise _ToolError(
            f"bcli {' '.join(args)} exited {proc.returncode}: {message}"
        )
