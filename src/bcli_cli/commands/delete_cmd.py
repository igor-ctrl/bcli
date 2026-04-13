"""bcli delete — delete records."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

from bcli.client._async import AsyncBCClient
from bcli_cli._state import state
from bcli_cli.output import print_context_banner

console = Console(stderr=True)


def delete_command(
    endpoint: str = typer.Argument(help="Entity set name"),
    record_id: str = typer.Argument(help="Record ID to delete"),
    etag: str = typer.Option("*", "--etag", help="ETag for optimistic concurrency"),
    publisher: Optional[str] = typer.Option(None, "--publisher"),
    group: Optional[str] = typer.Option(None, "--group"),
    version: Optional[str] = typer.Option(None, "--version"),
) -> None:
    """DELETE a record."""
    print_context_banner()

    if state.dry_run:
        console.print(f"[yellow]--dry-run: would DELETE {endpoint}({record_id})[/yellow]")
        raise typer.Exit()

    try:
        asyncio.run(
            _execute_delete(endpoint, record_id, etag=etag, publisher=publisher, group=group, version=version)
        )
        console.print(f"[green]✓[/green] Deleted {endpoint}({record_id})")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


async def _execute_delete(endpoint, record_id, **kwargs):
    async with AsyncBCClient(profile=state.profile_name, config=state.config) as client:
        return await client.delete(endpoint, record_id, **kwargs)
