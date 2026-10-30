"""bcli delete — delete records."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bcli_cli._envelope_wrap import validate_flags
from bcli_cli._state import state
from bcli_cli._write_cmd import run_write
from bcli_cli.output import print_context_banner

console = Console(stderr=True)


def delete_command(
    endpoint: str = typer.Argument(help="Entity set name"),
    record_id: str = typer.Argument(help="Record ID to delete"),
    etag: str = typer.Option("*", "--etag", help="ETag for optimistic concurrency"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format (unused, for flag consistency)"),
    publisher: Optional[str] = typer.Option(None, "--publisher", help="Custom API publisher override (escape hatch — registry resolves this automatically)"),
    group: Optional[str] = typer.Option(None, "--group", help="Custom API group override (escape hatch — registry resolves this automatically)"),
    version: Optional[str] = typer.Option(None, "--version", help="Custom API version override (escape hatch — registry resolves this automatically)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the read-only-profile warning prompt"),
    result_out: Optional[Path] = typer.Option(
        None,
        "--result-out",
        help="Write a JSON result envelope to this path (atomic). See AIP §Phase 2.",
    ),
    result_fd: Optional[int] = typer.Option(
        None,
        "--result-fd",
        help="Write the JSON result envelope to this file descriptor and close it.",
    ),
    idempotency_key: Optional[str] = typer.Option(
        None,
        "--idempotency-key",
        help="Opaque token forwarded as Idempotency-Key header (AIP §Phase 4d).",
    ),
) -> None:
    """DELETE a record."""
    validate_flags(result_out, result_fd)

    if format:
        state.format = format  # propagate subcommand -f to dry-run + audit
        if format in ("json", "csv", "ndjson", "raw"):
            state.quiet = True

    print_context_banner()

    run_write(
        "DELETE", endpoint, record_id=record_id, etag=etag,
        publisher=publisher, group=group, version=version,
        idempotency_key=idempotency_key,
        yes=yes, result_out=result_out, result_fd=result_fd,
    )
    console.print(f"[green]✓[/green] Deleted {endpoint}({record_id})")
