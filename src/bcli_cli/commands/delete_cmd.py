"""bcli delete — delete records."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

from bcli_cli._state import state
from bcli_cli.output import print_context_banner

console = Console(stderr=True)


def delete_command(
    endpoint: str = typer.Argument(help="Entity set name"),
    record_id: str = typer.Argument(help="Record ID to delete"),
    etag: str = typer.Option("*", "--etag", help="ETag for optimistic concurrency"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format (unused, for flag consistency)"),
    publisher: Optional[str] = typer.Option(None, "--publisher", hidden=True),
    group: Optional[str] = typer.Option(None, "--group", hidden=True),
    version: Optional[str] = typer.Option(None, "--version", hidden=True),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the read-only-profile warning prompt"),
) -> None:
    """DELETE a record."""
    if format and format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    from bcli_cli._safety import confirm_write_or_exit
    confirm_write_or_exit("DELETE", endpoint, yes=yes)

    if state.dry_run:
        from bcli_cli._dry_run import render_dry_run
        render_dry_run(
            "DELETE", endpoint, record_id=record_id,
            publisher=publisher, group=group, version=version,
            extra={"etag": etag},
        )

    try:
        asyncio.run(_audited_delete(
            endpoint, record_id,
            etag=etag, publisher=publisher, group=group, version=version,
        ))
        console.print(f"[green]✓[/green] Deleted {endpoint}({record_id})")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


async def _audited_delete(endpoint, record_id, **kwargs):
    from bcli_cli._audit_wrap import audited_write
    return await audited_write(
        _execute_delete(endpoint, record_id, **kwargs),
        method="DELETE", endpoint=endpoint, record_id=record_id,
    )


async def _execute_delete(endpoint, record_id, **kwargs):
    async with state.make_async_client() as client:
        return await client.delete(endpoint, record_id, **kwargs)
