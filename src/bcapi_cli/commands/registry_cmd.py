"""bcapi registry — custom API registry management."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bcapi.config._defaults import REGISTRIES_DIR
from bcapi.registry._importers import (
    import_from_json,
    import_from_postman,
    save_custom_registry,
)
from bcapi_cli._state import state

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("import")
def import_registry(
    from_postman: Optional[Path] = typer.Option(None, "--from-postman", help="Postman v2.1 collection JSON file"),
    from_json: Optional[Path] = typer.Option(None, "--from-json", help="Raw registry JSON file (bcapi or bcmcp format)"),
    from_metadata: bool = typer.Option(False, "--from-metadata", help="Query live BC $metadata endpoint (Phase 2)"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile to save registry for"),
) -> None:
    """Import custom API endpoints from Postman collection, JSON file, or $metadata."""
    profile_name = profile or state.active_profile_name

    if from_postman:
        if not from_postman.is_file():
            console.print(f"[red]File not found: {from_postman}[/red]")
            raise typer.Exit(1)

        console.print(f"[dim]Parsing Postman collection: {from_postman}[/dim]")
        endpoints = import_from_postman(from_postman)

        if not endpoints:
            console.print("[yellow]No custom API endpoints found in collection.[/yellow]")
            raise typer.Exit(1)

        path = save_custom_registry(profile_name, endpoints, source="postman")
        console.print(f"[green]✓[/green] Imported {len(endpoints)} custom endpoints")
        console.print(f"[dim]Saved to {path}[/dim]")

        # Show summary by group
        groups: dict[str, int] = {}
        for ep in endpoints:
            key = f"{ep.api_publisher}/{ep.api_group}/{ep.api_version}"
            groups[key] = groups.get(key, 0) + 1
        for route, count in sorted(groups.items()):
            console.print(f"  {route}: {count} endpoints")

    elif from_json:
        if not from_json.is_file():
            console.print(f"[red]File not found: {from_json}[/red]")
            raise typer.Exit(1)

        console.print(f"[dim]Importing from JSON: {from_json}[/dim]")
        endpoints = import_from_json(from_json)

        if not endpoints:
            console.print("[yellow]No endpoints found in file.[/yellow]")
            raise typer.Exit(1)

        path = save_custom_registry(profile_name, endpoints, source="json")
        console.print(f"[green]✓[/green] Imported {len(endpoints)} custom endpoints")
        console.print(f"[dim]Saved to {path}[/dim]")

    elif from_metadata:
        console.print("[yellow]$metadata import is not yet implemented (Phase 2).[/yellow]")
        console.print("[dim]Use --from-postman or --from-json for now.[/dim]")
        raise typer.Exit(1)

    else:
        console.print("[red]Specify one of: --from-postman, --from-json, --from-metadata[/red]")
        raise typer.Exit(1)


@app.command("list")
def list_registries() -> None:
    """Show imported custom registries."""
    if not REGISTRIES_DIR.is_dir():
        console.print("[dim]No custom registries found.[/dim]")
        return

    files = sorted(REGISTRIES_DIR.glob("*.json"))
    if not files:
        console.print("[dim]No custom registries found. Run 'bcapi registry import' to add one.[/dim]")
        return

    import json

    for f in files:
        profile_name = f.stem
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            count = data.get("endpoint_count", len(data.get("endpoints", [])))
            source = data.get("source", "unknown")
            imported = data.get("imported_at", "unknown")
            console.print(f"  [bold]{profile_name}[/bold]: {count} endpoints (source: {source}, imported: {imported})")
        except Exception:
            console.print(f"  [bold]{profile_name}[/bold]: [red]invalid file[/red]")
