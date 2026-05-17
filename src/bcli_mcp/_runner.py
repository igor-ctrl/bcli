"""Subprocess wrapper around ``bcli ... --format json``.

The MCP server's tools all delegate to the bcli CLI. This module owns the
process boundary: build the argv, set the env (so ``BCLI_PROFILE`` / a
per-call profile override are honoured), capture stdout + stderr, parse the
JSON response, and surface a clean ``ToolError`` on non-zero exits with
Rich markup stripped from the error message.

Two entry points:

* :func:`run_bcli_json` — read-only invocations. Append ``--format json``,
  parse stdout, return the parsed value.
* :func:`run_bcli_with_envelope` — mutating invocations. Append
  ``--result-out <tmp>`` and ``--format json``; return
  ``(exit_code, stdout, stderr)`` so the caller reads the envelope file
  back via :func:`bcli.result_envelope.read_envelope`. The envelope is
  the source of truth for outcome; stdout is mostly noise.

We deliberately do NOT import ``bcli`` Python modules here. Subprocess
delegation is the design — see ``docs/mcp-server.md``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Mapping

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


def _env_with_profile(profile: str | None) -> dict[str, str]:
    env = os.environ.copy()
    if profile:
        env["BCLI_PROFILE"] = profile
    return env


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

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env_with_profile(profile),
            check=False,
        )
    except FileNotFoundError as exc:
        raise _ToolError(
            "bcli executable not found on PATH. Install with "
            "'pip install bc-cli' or 'uv tool install bc-cli'."
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


def run_bcli_with_envelope(
    args: list[str],
    *,
    env: Mapping[str, str] | None,
    capture_envelope_path: str,
    timeout: float = 300.0,
) -> tuple[int, str, str]:
    """Run a mutating ``bcli`` command with ``--result-out <path>``.

    Returns ``(exit_code, stdout, stderr)``. The caller is expected to
    read the envelope file at ``capture_envelope_path`` via
    :func:`bcli.result_envelope.read_envelope` — that's the source of
    truth for the mutation outcome.

    The exit code is passed through so the caller can sanity-check (an
    envelope missing on disk + a non-zero exit code is "bcli crashed
    before writing the envelope" rather than "the mutation succeeded").
    """
    argv = ["bcli", *args, "--result-out", capture_envelope_path, "--format", "json"]
    effective_env = dict(env) if env is not None else os.environ.copy()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=effective_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise _ToolError(
            "bcli executable not found on PATH. Install with "
            "'pip install bc-cli' or 'uv tool install bc-cli'."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise _ToolError(
            f"bcli {' '.join(args)} timed out after {timeout}s"
        ) from exc

    return proc.returncode, proc.stdout, proc.stderr
