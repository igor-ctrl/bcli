"""bcli patch — update records."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

console = Console(stderr=True)


def patch_command(
    endpoint: str = typer.Argument(help="Entity set name"),
    record_id: str = typer.Argument(help="Record ID to update"),
    data: str = typer.Option(..., "--data", "-d", help="JSON data or @filename"),
    etag: str = typer.Option("*", "--etag", help="ETag for optimistic concurrency"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, json, csv, ndjson, raw"),
    publisher: Optional[str] = typer.Option(None, "--publisher", hidden=True),
    group: Optional[str] = typer.Option(None, "--group", hidden=True),
    version: Optional[str] = typer.Option(None, "--version", hidden=True),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the read-only-profile warning prompt"),
) -> None:
    """PATCH (update) an existing record."""
    output_format = format or state.format
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    from bcli_cli._safety import confirm_write_or_exit
    confirm_write_or_exit("PATCH", endpoint, yes=yes)

    body = _parse_data(data)

    if state.dry_run:
        from bcli_cli._dry_run import render_dry_run
        render_dry_run(
            "PATCH", endpoint, body=body, record_id=record_id,
            publisher=publisher, group=group, version=version,
            extra={"etag": etag},
        )

    try:
        result = asyncio.run(_audited_patch(
            endpoint, record_id, body,
            etag=etag, publisher=publisher, group=group, version=version,
        ))
        format_output([result] if result else [], output_format)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


async def _audited_patch(endpoint, record_id, body, **kwargs):
    from bcli_cli._audit_wrap import audited_write
    return await audited_write(
        _execute_patch(endpoint, record_id, body, **kwargs),
        method="PATCH", endpoint=endpoint, body=body, record_id=record_id,
    )


async def _execute_patch(endpoint, record_id, body, **kwargs):
    async with state.make_async_client() as client:
        return await client.patch(endpoint, record_id, body, **kwargs)


def _parse_data(data: str) -> dict:
    if data.startswith("@"):
        path = Path(data[1:])
        if not path.is_file():
            raise typer.BadParameter(f"File not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(data)
