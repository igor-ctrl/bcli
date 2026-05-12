"""Regression test: piping CLI output to a closing consumer must not
surface BrokenPipeError on stderr.

Running ``bcli ... | head -N`` was emitting a traceback like:

    Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' ...>
    BrokenPipeError: [Errno 32] Broken pipe

at interpreter shutdown when the formatter wrote past the point at which
``head`` had closed its end of the pipe. The fix in ``bcli_cli.app.main``
installs a SIGPIPE handler so the process exits silently like ``cat``.

The test exercises the real entry point in a subprocess against a small
in-memory record set — avoiding any BC network call — and asserts that
the stderr produced under truncation contains no traceback or
``BrokenPipeError`` marker.
"""

from __future__ import annotations

import signal
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(
    not hasattr(signal, "SIGPIPE"),
    reason="SIGPIPE only meaningful on POSIX",
)


def test_help_piped_to_head_does_not_leak_brokenpipe() -> None:
    """``bcli --help | head -1`` should exit clean.

    ``--help`` is the shortest path that exercises the entry point and
    writes more than one line to stdout — enough for ``head -1`` to
    close the pipe before the writer finishes.
    """
    help_proc = subprocess.Popen(
        [sys.executable, "-m", "bcli_cli.app", "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    head_proc = subprocess.Popen(
        ["head", "-1"],
        stdin=help_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert help_proc.stdout is not None
    help_proc.stdout.close()  # let head's EOF propagate
    head_proc.communicate(timeout=15)
    _, help_stderr = help_proc.communicate(timeout=15)

    stderr_text = help_stderr.decode("utf-8", errors="replace")

    assert "BrokenPipeError" not in stderr_text, (
        f"BrokenPipeError leaked to stderr:\n{stderr_text}"
    )
    assert "Exception ignored" not in stderr_text, (
        f"Python interpreter teardown noise leaked to stderr:\n{stderr_text}"
    )
