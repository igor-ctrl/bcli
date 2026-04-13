"""bcli batch — execute YAML batch files."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console

from bcli.client._async import AsyncBCClient
from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("run")
def run_batch(
    file: Path = typer.Argument(help="YAML batch file path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print resolved requests without executing"),
) -> None:
    """Execute a YAML batch file (sequence of API calls).

    Batch file format:

        name: "Post March 2026 Engine Utilization"
        steps:
          - action: get
            endpoint: engineOverviews
            params:
              filter: "engineModel eq 'CF34-10E'"
              top: 5
          - action: post
            endpoint: engineUtilizations
            data:
              esn: "ESN-123456"
              period: "2026-03"
              flightHours: 350.5
    """
    print_context_banner()

    if not file.is_file():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    try:
        import yaml
    except ImportError:
        console.print("[red]PyYAML is required for batch mode.[/red]")
        console.print("[dim]Install it: pip install pyyaml[/dim]")
        raise typer.Exit(1)

    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    batch_name = raw.get("name", file.stem)
    steps = raw.get("steps", [])

    if not steps:
        console.print("[yellow]No steps found in batch file.[/yellow]")
        raise typer.Exit()

    console.print(f"[bold]Batch:[/bold] {batch_name}")
    console.print(f"[dim]{len(steps)} step(s)[/dim]\n")

    if dry_run or state.dry_run:
        for i, step in enumerate(steps, 1):
            action = step.get("action", "get").upper()
            endpoint = step.get("endpoint", "?")
            data = step.get("data")
            params = step.get("params", {})
            console.print(f"  [dim]Step {i}:[/dim] {action} {endpoint}")
            if params:
                console.print(f"    [dim]Params: {params}[/dim]")
            if data:
                console.print(f"    [dim]Data: {json.dumps(data, default=str)}[/dim]")
        console.print(f"\n[yellow]--dry-run: {len(steps)} step(s) would execute.[/yellow]")
        return

    try:
        results = asyncio.run(_execute_batch(steps))
        console.print(f"\n[green]✓[/green] Batch complete: {len(results)}/{len(steps)} steps succeeded")
    except Exception as e:
        console.print(f"[red]Batch failed:[/red] {e}")
        raise typer.Exit(1)


async def _execute_batch(steps: list[dict]) -> list[dict]:
    results = []
    async with AsyncBCClient(profile=state.profile_name, config=state.config) as client:
        for i, step in enumerate(steps, 1):
            action = step.get("action", "get").lower()
            endpoint = step.get("endpoint", "")
            data = step.get("data")
            params = step.get("params", {})
            record_id = step.get("id")
            etag = step.get("etag", "*")

            console.print(f"  [dim]Step {i}:[/dim] {action.upper()} {endpoint}...", end=" ")

            try:
                if action == "get":
                    from bcli.odata._query import Query
                    query = Query()
                    if params.get("filter"):
                        query.filter(params["filter"])
                    if params.get("select"):
                        query.select(*params["select"].split(","))
                    if params.get("top"):
                        query.top(int(params["top"]))
                    if params.get("orderby"):
                        query.orderby(params["orderby"])

                    response = await client.get(endpoint, record_id, query=query)
                    count = len(response.value) if not record_id else 1
                    console.print(f"[green]✓[/green] {count} record(s)")
                    results.append({"step": i, "status": "ok", "records": count})

                elif action == "post":
                    result = await client.post(endpoint, data or {})
                    console.print("[green]✓[/green] created")
                    results.append({"step": i, "status": "ok", "result": result})

                elif action == "patch":
                    if not record_id:
                        console.print("[red]✗ missing 'id' field[/red]")
                        results.append({"step": i, "status": "error", "error": "missing id"})
                        continue
                    result = await client.patch(endpoint, record_id, data or {}, etag=etag)
                    console.print("[green]✓[/green] updated")
                    results.append({"step": i, "status": "ok", "result": result})

                elif action == "delete":
                    if not record_id:
                        console.print("[red]✗ missing 'id' field[/red]")
                        results.append({"step": i, "status": "error", "error": "missing id"})
                        continue
                    await client.delete(endpoint, record_id, etag=etag)
                    console.print("[green]✓[/green] deleted")
                    results.append({"step": i, "status": "ok"})

                else:
                    console.print(f"[yellow]? unknown action '{action}'[/yellow]")
                    results.append({"step": i, "status": "skipped"})

            except Exception as e:
                console.print(f"[red]✗ {e}[/red]")
                results.append({"step": i, "status": "error", "error": str(e)})

    return results
