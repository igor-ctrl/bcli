"""bcli post — create records."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bcli.client._async import AsyncBCClient
from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

console = Console(stderr=True)


def post_command(
    endpoint: str = typer.Argument(help="Entity set name"),
    data: str = typer.Option(..., "--data", "-d", help="JSON data or @filename"),
    publisher: Optional[str] = typer.Option(None, "--publisher"),
    group: Optional[str] = typer.Option(None, "--group"),
    version: Optional[str] = typer.Option(None, "--version"),
) -> None:
    """POST (create) a new record."""
    print_context_banner()

    body = _parse_data(data)

    if state.dry_run:
        console.print(f"[yellow]--dry-run: would POST to {endpoint}[/yellow]")
        console.print(json.dumps(body, indent=2))
        raise typer.Exit()

    try:
        result = asyncio.run(
            _execute_post(endpoint, body, publisher=publisher, group=group, version=version)
        )
        format_output([result] if result else [], state.format)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


async def _execute_post(endpoint, body, **kwargs):
    async with AsyncBCClient(profile=state.profile_name, config=state.config) as client:
        return await client.post(endpoint, body, **kwargs)


def _parse_data(data: str) -> dict:
    """Parse --data argument: JSON string or @filename."""
    if data.startswith("@"):
        path = Path(data[1:])
        if not path.is_file():
            raise typer.BadParameter(f"File not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(data)
