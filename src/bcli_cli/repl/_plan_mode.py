"""Plan-mode promotion: a drafted batch YAML → reviewed → run.

When plan mode is active the agent's write tier is replaced by the
``draft_batch`` tool, which renders a bcli batch YAML (writing nothing).
This module turns that drafted YAML into a file the operator can review
and then promotes it through the *real* gated path — ``bcli batch run``
— exactly as ``bcli extract`` does. No write ever bypasses the batch
runner's own safety (disable_writes, production confirm, audit log).

The functions here are deliberately UI-free so they unit-test without
Textual: the app calls :func:`write_draft` then, on confirm,
:func:`run_batch`.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path


def write_draft(batch_yaml: str, *, name: str = "agent-plan") -> Path:
    """Persist a drafted batch YAML to a temp file for review. Returns path."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "plan"
    fd, path_str = tempfile.mkstemp(prefix=f"bcli-{safe}-", suffix=".batch.yaml")
    path = Path(path_str)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(batch_yaml)
    return path


async def run_batch(
    path: Path,
    *,
    profile_name: str = "",
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Run a batch YAML through the real ``bcli batch run`` CLI.

    Returns ``(ok, output)``. ``--yes`` is only passed for real runs
    (the human already confirmed in the REPL); dry-runs never mutate.
    The batch runner re-applies its own disable_writes / production
    gating, so this is defense-in-depth, not a bypass.
    """
    if shutil.which("bcli"):
        argv = ["bcli"]
    else:
        argv = [sys.executable, "-m", "bcli_cli.app"]
    if profile_name:
        argv += ["--profile", profile_name]
    argv += ["batch", "run", str(path), "--format", "json"]
    argv.append("--dry-run" if dry_run else "--yes")

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    if proc.returncode != 0:
        return False, err or out or f"batch run exited {proc.returncode}"
    return True, out or err


__all__ = ["run_batch", "write_draft"]
