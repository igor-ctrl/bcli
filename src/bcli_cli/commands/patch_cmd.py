"""bcli patch — update records."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from bcli_cli._data_arg import parse_data_argument
from bcli_cli._envelope_wrap import validate_flags
from bcli_cli._state import state
from bcli_cli._write_cmd import run_write
from bcli_cli.output import format_output, print_context_banner


def patch_command(
    endpoint: str = typer.Argument(help="Entity set name"),
    record_id: str = typer.Argument(help="Record ID to update"),
    data: str = typer.Option(..., "--data", "-d", help="JSON data or @filename"),
    etag: str = typer.Option("*", "--etag", help="ETag for optimistic concurrency"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, json, csv, ndjson, raw"),
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
    """PATCH (update) an existing record."""
    validate_flags(result_out, result_fd)

    output_format = format or state.format
    state.format = output_format  # propagate subcommand -f to dry-run + audit
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    result = run_write(
        "PATCH", endpoint, body=parse_data_argument(data), record_id=record_id, etag=etag,
        publisher=publisher, group=group, version=version,
        idempotency_key=idempotency_key,
        yes=yes, result_out=result_out, result_fd=result_fd,
    )
    format_output([result.body] if result.body else [], output_format)
