"""bcli registry — custom API registry management."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bcli.config._defaults import REGISTRIES_DIR
from bcli.exit_codes import EXIT_NOT_FOUND, EXIT_USAGE, EXIT_VALIDATION
from bcli.registry._importers import (
    export_custom_registry,
    import_from_file,
    import_from_metadata,
    import_from_postman,
    save_custom_registry,
)
from bcli.registry._schema import EndpointMetadata
from bcli_cli._state import state

app = typer.Typer(no_args_is_help=True)
console = Console()


def _is_postman_collection(path: Path) -> bool:
    if path.suffix.lower() != ".json":
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return False
    return isinstance(raw, dict) and "info" in raw and "item" in raw


def _load_file(path: Path) -> tuple[list[EndpointMetadata], str]:
    if not path.is_file():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(EXIT_NOT_FOUND)
    if _is_postman_collection(path):
        console.print(f"[dim]Parsing Postman collection: {path}[/dim]")
        return import_from_postman(path), "postman"
    console.print(f"[dim]Importing registry file: {path}[/dim]")
    try:
        return import_from_file(path), "file"
    except (ValueError, json.JSONDecodeError) as e:
        console.print(f"[red]Invalid registry file {path}:[/red] {e}")
        raise typer.Exit(EXIT_VALIDATION) from e


def _load_metadata(
    publisher: Optional[str], group: Optional[str], version: Optional[str],
) -> list[EndpointMetadata]:
    p = state.profile
    publisher = publisher or p.api_publisher
    group = group or p.api_group
    version = version or p.api_version
    if not (publisher and group and version):
        console.print(
            "[red]--from-metadata needs the API route: pass --publisher, --group"
            " and --version (the values from your AL API page).[/red]"
        )
        raise typer.Exit(EXIT_USAGE)

    console.print(f"[dim]Querying $metadata for {publisher}/{group}/{version}...[/dim]")

    async def _run() -> list[EndpointMetadata]:
        async with state.make_async_client() as client:
            transport = client._ensure_transport()
            return await import_from_metadata(
                transport, p.environment, publisher, group, version,
            )

    return asyncio.run(_run())


@app.command("import")
def import_registry(
    from_metadata: bool = typer.Option(
        False, "--from-metadata",
        help="Discover every endpoint of one custom API route from live BC $metadata",
    ),
    publisher: Optional[str] = typer.Option(
        None, "--publisher", help="APIPublisher of your AL API pages (with --from-metadata)",
    ),
    group: Optional[str] = typer.Option(
        None, "--group", help="APIGroup of your AL API pages (with --from-metadata)",
    ),
    version: Optional[str] = typer.Option(
        None, "--version", help="APIVersion of your AL API pages (with --from-metadata)",
    ),
    from_file: Optional[Path] = typer.Option(
        None, "--from-file",
        help="Registry file (JSON or YAML) or Postman v2.1 collection",
    ),
    from_postman: Optional[Path] = typer.Option(None, "--from-postman", hidden=True),
    from_json: Optional[Path] = typer.Option(None, "--from-json", hidden=True),
    replace: bool = typer.Option(
        False, "--replace",
        help="Drop previously imported endpoints instead of merging with them",
    ),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile to save registry for"),
) -> None:
    """Add custom API endpoints to a profile's registry.

    Imports merge with what is already registered, so you can run this
    once per API route.
    """
    profile_name = profile or state.active_profile_name
    path_arg = from_file or from_postman or from_json

    if from_metadata and path_arg:
        console.print("[red]Use either --from-metadata or --from-file, not both.[/red]")
        raise typer.Exit(EXIT_USAGE)

    if from_metadata:
        endpoints = _load_metadata(publisher, group, version)
        source = "metadata"
    elif path_arg:
        endpoints, source = _load_file(path_arg)
    else:
        console.print(
            "[red]Specify --from-metadata --publisher P --group G --version V,"
            " or --from-file PATH.[/red]"
        )
        raise typer.Exit(EXIT_USAGE)

    if not endpoints:
        console.print("[yellow]No custom API endpoints found.[/yellow]")
        raise typer.Exit(EXIT_NOT_FOUND)

    path = save_custom_registry(profile_name, endpoints, source=source, replace=replace)
    console.print(f"[green]✓[/green] Imported {len(endpoints)} endpoint(s) into profile '{profile_name}'")
    routes: dict[str, int] = {}
    for ep in endpoints:
        routes[ep.route_display] = routes.get(ep.route_display, 0) + 1
    for route, count in sorted(routes.items()):
        console.print(f"  {route}: {count}")
    console.print(f"[dim]Saved to {path}[/dim]")


@app.command("export")
def export_registry(
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write to this file instead of stdout",
    ),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile to export"),
) -> None:
    """Export a profile's custom endpoints as a portable registry file.

    Commit the file next to your AL extension or hand it to a teammate;
    they load it with `bcli registry import --from-file`.
    """
    profile_name = profile or state.active_profile_name
    data = export_custom_registry(profile_name)
    if not data["endpoints"]:
        console.print(f"[yellow]Profile '{profile_name}' has no custom endpoints to export.[/yellow]")
        raise typer.Exit(EXIT_NOT_FOUND)

    text = json.dumps(data, indent=2) + "\n"
    if output is None:
        typer.echo(text, nl=False)
        return
    output.write_text(text, encoding="utf-8")
    console.print(f"[green]✓[/green] Exported {len(data['endpoints'])} endpoint(s) to {output}")


@app.command("list")
def list_registries() -> None:
    """Show imported custom registries."""
    if not REGISTRIES_DIR.is_dir():
        console.print("[dim]No custom registries found.[/dim]")
        return

    files = sorted(REGISTRIES_DIR.glob("*.json"))
    if not files:
        console.print("[dim]No custom registries found. Run 'bcli registry import' to add one.[/dim]")
        return

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
