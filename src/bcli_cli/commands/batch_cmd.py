"""bcli batch — execute YAML batch files with optional workflow features."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

app = typer.Typer(no_args_is_help=True)
console = Console()


# ─── Helpers ─────────────────────────────────────────────────────────


def _smart_parse_value(raw: str) -> Any:
    """Parse a --set value string, preserving types via yaml.safe_load.

    ``"4500"`` → int, ``"3.14"`` → float, ``"true"`` → bool,
    ``"V00011"`` → str.
    """
    import yaml

    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _parse_set_params(set_params: list[str] | None) -> dict[str, Any]:
    """Parse ``--set key=value`` pairs into a dict with auto-typed values."""
    if not set_params:
        return {}
    result: dict[str, Any] = {}
    for item in set_params:
        if "=" not in item:
            console.print(f"[red]Invalid --set format: '{item}' (expected key=value)[/red]")
            raise typer.Exit(1)
        key, _, raw_value = item.partition("=")
        result[key.strip()] = _smart_parse_value(raw_value.strip())
    return result


def _load_params_file(params_file: Path) -> dict[str, Any]:
    """Load a flat YAML file as workflow parameters."""
    import yaml

    if not params_file.is_file():
        console.print(f"[red]Params file not found: {params_file}[/red]")
        raise typer.Exit(1)
    raw = yaml.safe_load(params_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        console.print("[red]Params file must be a YAML mapping (key: value).[/red]")
        raise typer.Exit(1)
    return raw


def _has_references(obj: Any) -> bool:
    """Return True if any string in the structure contains ``${{ ``."""
    if isinstance(obj, str):
        return "${{" in obj
    if isinstance(obj, dict):
        return any(_has_references(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_references(item) for item in obj)
    return False


def _build_workflow_params(
    raw: dict,
    set_params: list[str] | None,
    params_file: Path | None,
) -> dict[str, Any]:
    """Merge declared defaults, --params file, and --set overrides."""
    from bcli.workflow._models import ParamDef

    # Start with declared defaults
    params: dict[str, Any] = {}
    declared = raw.get("params") or {}
    for key, defn in declared.items():
        if isinstance(defn, dict):
            p = ParamDef(**defn)
        else:
            # Shorthand: params: {vendor_no: "V00011"} → treat as default
            p = ParamDef(default=defn, required=False)
        if p.default is not None:
            params[key] = p.default

    # Layer: params file
    if params_file:
        params.update(_load_params_file(params_file))

    # Layer: --set overrides (highest priority)
    params.update(_parse_set_params(set_params))

    # Validate required params are present
    for key, defn in declared.items():
        if isinstance(defn, dict):
            p = ParamDef(**defn)
        else:
            p = ParamDef(default=defn, required=False)
        if p.required and key not in params:
            console.print(f"[red]Required parameter '{key}' not provided. Pass --set {key}=<value>[/red]")
            raise typer.Exit(1)

    return params


def _validate_step_names(steps: list[dict]) -> None:
    """Ensure step names are unique and auto-assign missing ones."""
    seen: set[str] = set()
    for i, step in enumerate(steps):
        name = step.get("name") or f"step_{i + 1}"
        step["name"] = name
        if name in seen:
            console.print(f"[red]Duplicate step name: '{name}'[/red]")
            raise typer.Exit(1)
        seen.add(name)


# ─── Command ─────────────────────────────────────────────────────────


@app.command("run")
def run_batch(
    file: Path = typer.Argument(help="YAML batch file path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print resolved requests without executing"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Save full results to JSON file"),
    format: str | None = typer.Option(None, "--format", "-f", help="Print each step's data (table, json, csv, ndjson)"),
    set_params: list[str] | None = typer.Option(None, "--set", help="Set parameter: key=value (repeatable)"),
    params_file: Path | None = typer.Option(None, "--params", help="YAML file with parameter values"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the read-only-profile prompt for mutating batch steps"),
) -> None:
    """Execute a YAML batch file (sequence of API calls).

    Supports workflow features: step chaining via ${{ steps.<name>.<field> }}
    and runtime parameters via ${{ params.<key> }}.

    \b
    Examples:
        bcli batch run workflow.yaml --set vendor_no=V00011
        bcli batch run workflow.yaml --params values.yaml
        bcli batch run workflow.yaml --set vendor_no=V00011 --dry-run
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

    # Detect workflow mode: ${{ }} references OR top-level params OR --set/--params flags
    is_workflow = (
        "params" in raw
        or set_params
        or params_file is not None
        or _has_references(steps)
    )

    context = None
    if is_workflow:
        from bcli.workflow._models import WorkflowContext

        params = _build_workflow_params(raw, set_params, params_file)
        context = WorkflowContext(params=params)
        _validate_step_names(steps)

    console.print(f"[bold]Batch:[/bold] {batch_name}")
    if is_workflow and context and context.params:
        console.print(f"[dim]Params: {context.params}[/dim]")
    console.print(f"[dim]{len(steps)} step(s)[/dim]\n")

    if dry_run or state.dry_run:
        _print_dry_run(steps, context)
        return

    # Apply the same disable_writes gate that direct post/patch/delete commands
    # use. Without this, a profile marked read-only would still execute
    # mutating batch steps in non-interactive automation — see vuln-0002.
    # We inspect raw steps here (workflow `${{ }}` references are resolved
    # later inside `_execute_batch`); any step that statically declares a
    # mutating action triggers a single batch-level confirmation.
    mutating_actions = {"post", "patch", "delete"}
    mutating_steps = [
        step for step in steps
        if (step.get("action") or "get").lower() in mutating_actions
    ]
    if mutating_steps:
        from bcli_cli._safety import confirm_write_or_exit

        preview = ", ".join(
            f"{(step.get('action') or 'get').upper()} {step.get('endpoint', '?')}"
            for step in mutating_steps[:3]
        )
        if len(mutating_steps) > 3:
            preview += f", +{len(mutating_steps) - 3} more"
        confirm_write_or_exit("BATCH WRITE", preview, yes=yes)

    output_format = format

    try:
        results = asyncio.run(_execute_batch(steps, context=context, output_format=output_format))

        succeeded = sum(1 for r in results if r.get("status") == "ok")
        console.print(f"\n[green]✓[/green] Batch complete: {succeeded}/{len(steps)} steps succeeded")

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output_data = {
                "batch": batch_name,
                "steps": results,
            }
            output.write_text(json.dumps(output_data, indent=2, default=str))
            console.print(f"[dim]Results saved to {output}[/dim]")

    except Exception as e:
        console.print(f"[red]Batch failed:[/red] {e}")
        raise typer.Exit(1)


# ─── Dry run ─────────────────────────────────────────────────────────


def _print_dry_run(steps: list[dict], context: Any | None) -> None:
    """Print resolved (or partially resolved) requests without executing."""
    from bcli.workflow._resolver import resolve_references

    for i, step in enumerate(steps, 1):
        step_to_show = step
        if context is not None:
            # Resolve params (known at dry-run time); step refs will fail,
            # so we resolve what we can and leave the rest as-is.
            try:
                step_to_show = resolve_references(step, context)
            except Exception:
                # Step references can't resolve at dry-run time — show raw
                step_to_show = step

        action = (step_to_show.get("action") or "get").upper()
        endpoint = step_to_show.get("endpoint", "?")
        name = step_to_show.get("name", "")
        data = step_to_show.get("data")
        params = step_to_show.get("params", {})

        label = f"  [dim]Step {i}:[/dim] {action} {endpoint}"
        if name and name != endpoint:
            label += f" [dim]({name})[/dim]"
        console.print(label)

        if params:
            console.print(f"    [dim]Params: {params}[/dim]")
        if data:
            console.print(f"    [dim]Data: {json.dumps(data, default=str)}[/dim]")

    console.print(f"\n[yellow]--dry-run: {len(steps)} step(s) would execute.[/yellow]")


# ─── Execution engine ────────────────────────────────────────────────


async def _execute_batch(
    steps: list[dict],
    *,
    context: Any | None = None,
    output_format: str | None = None,
) -> list[dict]:
    from bcli.workflow._models import StepResult, WorkflowContext
    from bcli.workflow._resolver import resolve_references

    results = []
    async with state.make_async_client() as client:
        for i, step in enumerate(steps, 1):
            # Resolve workflow references if in workflow mode
            if context is not None:
                try:
                    step = resolve_references(step, context)
                except Exception as e:
                    step_name = step.get("name", f"step_{i}")
                    console.print(f"  [dim]Step {i}:[/dim] [red]✗ {e}[/red]")
                    results.append({"step": i, "name": step_name, "status": "error", "error": str(e)})
                    if isinstance(context, WorkflowContext):
                        context.set_result(
                            step_name,
                            StepResult(name=step_name, action="?", endpoint="?", status="error", error=str(e)),
                        )
                    continue

            action = (step.get("action") or "get").lower()
            endpoint = step.get("endpoint", "")
            step_name = step.get("name") or endpoint
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
                        query.select(*[s.strip() for s in params["select"].split(",")])
                    if params.get("top"):
                        query.top(int(params["top"]))
                    if params.get("orderby"):
                        query.orderby(params["orderby"])

                    response = await client.get(endpoint, record_id, query=query)
                    records = response.value if not record_id else [response.raw] if response.raw else []
                    console.print(f"[green]✓[/green] {len(records)} record(s)")
                    result_entry = {"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "ok", "count": len(records), "data": records}
                    results.append(result_entry)

                    if isinstance(context, WorkflowContext):
                        context.set_result(
                            step_name,
                            StepResult(name=step_name, action=action, endpoint=endpoint, status="ok", data=records),
                        )

                    if output_format and records:
                        console.print(f"  [bold]── {step_name} ──[/bold]")
                        format_output(records, output_format)
                        console.print()

                elif action == "post":
                    result = await client.post(endpoint, data or {})
                    console.print("[green]✓[/green] created")
                    result_entry = {"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "ok", "data": [result] if result else []}
                    results.append(result_entry)

                    if isinstance(context, WorkflowContext):
                        context.set_result(
                            step_name,
                            StepResult(name=step_name, action=action, endpoint=endpoint, status="ok", data=result or {}),
                        )

                    if output_format and result:
                        format_output([result], output_format)

                elif action == "patch":
                    if not record_id:
                        console.print("[red]✗ missing 'id' field[/red]")
                        results.append({"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "error", "error": "missing id"})
                        continue
                    result = await client.patch(endpoint, record_id, data or {}, etag=etag)
                    console.print("[green]✓[/green] updated")
                    result_entry = {"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "ok", "data": [result] if result else []}
                    results.append(result_entry)

                    if isinstance(context, WorkflowContext):
                        context.set_result(
                            step_name,
                            StepResult(name=step_name, action=action, endpoint=endpoint, status="ok", data=result or {}),
                        )

                elif action == "delete":
                    if not record_id:
                        console.print("[red]✗ missing 'id' field[/red]")
                        results.append({"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "error", "error": "missing id"})
                        continue
                    await client.delete(endpoint, record_id, etag=etag)
                    console.print("[green]✓[/green] deleted")
                    result_entry = {"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "ok"}
                    results.append(result_entry)

                    if isinstance(context, WorkflowContext):
                        context.set_result(
                            step_name,
                            StepResult(name=step_name, action=action, endpoint=endpoint, status="ok", data={}),
                        )

                else:
                    console.print(f"[yellow]? unknown action '{action}'[/yellow]")
                    results.append({"step": i, "name": step_name, "status": "skipped"})

            except Exception as e:
                console.print(f"[red]✗ {e}[/red]")
                results.append({"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "error", "error": str(e)})
                if isinstance(context, WorkflowContext):
                    context.set_result(
                        step_name,
                        StepResult(name=step_name, action=action, endpoint=endpoint, status="error", error=str(e)),
                    )

    return results
