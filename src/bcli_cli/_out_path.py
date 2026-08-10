"""Destination-path handling for the ``--out`` flag.

``bcli get --out`` and ``bcli action --out`` both take a path from the user and
put bytes there. They share this module so the two verbs answer "may I write
here?" identically — and, more importantly, so both answer it *before* touching
the network. For ``action`` that ordering is the whole point: the POST it is
about to send can change BC, and discovering an unwritable destination
afterwards would leave a mutation applied with its payload nowhere to go.

The refusal-by-default policy matches ``bcli extract`` (``_check_writeable``):
an existing file is never replaced without ``--overwrite``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import typer
from rich.console import Console

console = Console(stderr=True)


def prepare_out_path(path: Path, *, overwrite: bool) -> Path:
    """Expand ``path`` and confirm it is safe to write, or exit non-zero.

    Returns the expanded path. Raises ``typer.Exit(1)`` when the file exists
    and ``--overwrite`` was not passed, or when the parent directory is
    missing — the directory is never created, because a typo'd path is far
    more likely than a genuinely wanted new tree.
    """
    dest = path.expanduser()

    if dest.exists() and not overwrite:
        console.print(
            f"[red]Refusing to overwrite[/red] {dest} — pass [bold]--overwrite[/bold] "
            "to replace it."
        )
        raise typer.Exit(1)

    if not dest.parent.exists():
        console.print(
            f"[red]Error:[/red] directory {dest.parent} does not exist. Create it first "
            "(bcli won't), then re-run."
        )
        raise typer.Exit(1)

    return dest


def atomic_write_bytes(dest: Path, raw: bytes) -> None:
    """Write ``raw`` to ``dest`` via a temp file + :func:`os.replace`.

    Same discipline as ``BCTransport.download``: a reader never sees a
    half-written file, and a failed write leaves no part file behind.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp_name, dest)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
