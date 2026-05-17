"""bcli batch — execute YAML batch files with optional workflow features.

This module also hosts the durable operation ledger introduced in AIP
v0.1 Phase 3.  Every batch run writes:

  * a ``run`` row at start_run time, with manifest hash + profile/env/company;
  * an *intent* ``step`` row before each HTTP call (survives SIGKILL);
  * an *outcome* ``step`` row after the call returns (or raises).

``bcli batch state <run-id>``, ``bcli batch list``, and ``bcli batch
rollback <run-id>`` read that ledger.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bcli.batch.ledger import Ledger
from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

app = typer.Typer(no_args_is_help=True)
console = Console()
# Stderr console for AIP metadata (ledger path, run id) — keeps stdout
# clean for downstream parsers / pipelines as required by the
# "behavior unchanged by default" constraint.
_stderr = Console(stderr=True)


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


def _close_if_coroutine(obj: Any) -> None:
    """Close a stray coroutine to suppress the ResourceWarning that fires
    when an ``AsyncMock``'s synchronously-accessed attribute returns a
    coroutine the production code never awaits. No-op for non-coroutines.
    """
    if hasattr(obj, "close") and callable(obj.close):
        # ``inspect.iscoroutine`` is stricter than necessary — duck-type
        # on ``send`` / ``close`` to also cover generator-coroutines.
        if hasattr(obj, "send") or type(obj).__name__ in {"coroutine"}:
            try:
                obj.close()
            except Exception:
                pass


def _body_hash(body: Any) -> str | None:
    """sha256 of a JSON-serialised body, or None if there's no body.

    Hashing instead of storing the body keeps the ledger compact and
    avoids accidentally durably persisting secrets that find their way
    into request bodies. Reviewers can confirm "this is what we sent"
    by hashing the YAML themselves.
    """
    if body is None:
        return None
    try:
        encoded = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _manifest_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compose_rollback_url(client: Any, entity: str, post_result: Any) -> str | None:
    """Build the URL that DELETE will hit on rollback.

    BC POST responses include ``id`` or ``systemId`` for the new
    record. We resolve the entity URL via the client's registry
    (so a sandboxed profile's custom route is honoured), then append
    ``(<id>)`` per OData. If the result lacks an id, rollback isn't
    possible from this run alone — return ``None`` and the rollback
    command will mark the step ``rollback_skipped``.
    """
    if not isinstance(post_result, dict):
        return None
    record_id = post_result.get("id") or post_result.get("systemId")
    if not record_id:
        return None
    try:
        base = client._resolve_url(entity)  # noqa: SLF001
    except Exception:
        return None
    if not isinstance(base, str):
        _close_if_coroutine(base)
        return None
    # OData record URL convention: <entitySet>(<id>)
    return f"{base}({record_id})"


# ─── Command: batch run ──────────────────────────────────────────────


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

    Every run writes a durable SQLite ledger at
    ``~/.config/bcli/batch/<run-id>.db`` — see ``bcli batch state`` and
    ``bcli batch rollback`` to inspect or undo it.

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
        import yaml  # noqa: F401  (still used by helpers; load_workflow_yaml wraps the workflow body)
    except ImportError:
        console.print("[red]PyYAML is required for batch mode.[/red]")
        console.print("[dim]Install it: pip install pyyaml[/dim]")
        raise typer.Exit(1)

    from bcli.errors import WorkflowError
    from bcli.workflow import load_workflow_yaml

    try:
        raw = load_workflow_yaml(file)
    except WorkflowError as e:
        console.print(f"[red]Invalid workflow YAML:[/red] {e}")
        raise typer.Exit(1)
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

    # Spin up the ledger and write the run row BEFORE any HTTP fires.
    # The run-id is a fresh uuid4 so we never collide with a prior run's
    # ledger file. A defensive guard in start_run raises if it does.
    run_id = uuid.uuid4().hex
    ledger = Ledger(run_id=run_id)
    ledger.start_run(
        manifest_path=str(file.resolve()),
        manifest_hash=_manifest_hash(file),
        profile=state.active_profile_name,
        environment=state.profile.environment,
        company=state.profile.company_id or "",
    )
    _stderr.print(f"[dim]Ledger: {ledger.db_path}[/dim]")

    final_state = "completed"
    try:
        results = asyncio.run(
            _execute_batch(steps, context=context, output_format=output_format, ledger=ledger)
        )

        succeeded = sum(1 for r in results if r.get("status") == "ok")
        failed_count = sum(1 for r in results if r.get("status") == "error")
        if failed_count and succeeded:
            final_state = "partially_committed"
        elif failed_count:
            final_state = "failed"
        else:
            final_state = "completed"
        console.print(f"\n[green]✓[/green] Batch complete: {succeeded}/{len(steps)} steps succeeded")
        _stderr.print(f"[dim]Run id: {run_id}[/dim]")

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output_data = {
                "batch": batch_name,
                "run_id": run_id,
                "steps": results,
            }
            output.write_text(json.dumps(output_data, indent=2, default=str))
            console.print(f"[dim]Results saved to {output}[/dim]")

    except BaseException as e:
        # BaseException covers SystemExit / KeyboardInterrupt — anything
        # short of SIGKILL passes through this branch, so the ledger
        # always gets a derived final state.
        if isinstance(e, Exception) and not isinstance(e, (KeyboardInterrupt, SystemExit)):
            console.print(f"[red]Batch failed:[/red] {e}")
        # Derive the truthful state from steps that landed before the
        # crash. ``compute_run_state`` looks at intent_ts vs outcome_ts
        # — exactly what we need for "POST committed, then we died."
        final_state = ledger.compute_run_state(run_id)
        ledger.finish_run(run_id, final_state)
        ledger.close()
        if isinstance(e, Exception) and not isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise typer.Exit(1)
        raise

    ledger.finish_run(run_id, final_state)
    ledger.close()


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
    ledger: Ledger | None = None,
) -> list[dict]:
    """Execute the batch steps, writing intent + outcome rows around each
    HTTP call when a ``ledger`` is supplied.

    The ``ledger`` argument is keyword-only and optional so existing
    integration tests that call this function directly continue to work
    unchanged.
    """
    from bcli.workflow._models import StepResult, WorkflowContext
    from bcli.workflow._resolver import resolve_references

    results: list[dict] = []
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

            # ── Ledger: write intent BEFORE the HTTP call. ──
            # The resolved URL is computed lazily via the client's
            # registry. If resolution fails (or the client is a mock
            # whose ``_resolve_url`` returns something non-string), fall
            # back to the entity name so the operator has *something* to
            # look at and the SQLite TEXT column accepts the value.
            ledger_step_id: int | None = None
            if ledger is not None:
                intent_url = endpoint
                if endpoint:
                    try:
                        candidate = client._resolve_url(endpoint, record_id=record_id)  # noqa: SLF001
                        if isinstance(candidate, str):
                            intent_url = candidate
                        else:
                            # AsyncMock returns a coroutine when treated
                            # as sync — close it to avoid the resource
                            # warning at GC time.
                            _close_if_coroutine(candidate)
                    except Exception:
                        intent_url = endpoint
                ledger_step_id = ledger.write_intent(
                    seq=i,
                    method=action.upper() if action != "get" else "GET",
                    url=intent_url,
                    body_hash=_body_hash(data) if data is not None else None,
                )

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

                    if ledger is not None and ledger_step_id is not None:
                        ledger.write_outcome(
                            step_id=ledger_step_id, status="committed",
                            bc_correlation_id=None, error_message=None,
                            rollback_url=None,  # GETs are not rollback-eligible
                        )

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

                    if ledger is not None and ledger_step_id is not None:
                        rb_url = _compose_rollback_url(client, endpoint, result)
                        ledger.write_outcome(
                            step_id=ledger_step_id, status="committed",
                            bc_correlation_id=None, error_message=None,
                            rollback_url=rb_url,
                        )

                elif action == "patch":
                    if not record_id:
                        console.print("[red]✗ missing 'id' field[/red]")
                        results.append({"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "error", "error": "missing id"})
                        if ledger is not None and ledger_step_id is not None:
                            ledger.write_outcome(
                                step_id=ledger_step_id, status="failed",
                                bc_correlation_id=None,
                                error_message="missing id",
                                rollback_url=None,
                            )
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

                    if ledger is not None and ledger_step_id is not None:
                        ledger.write_outcome(
                            step_id=ledger_step_id, status="committed",
                            bc_correlation_id=None, error_message=None,
                            # PATCH rollback would need a pre-image snapshot
                            # we don't keep in v0.1 — see help text on
                            # ``bcli batch rollback``.
                            rollback_url=None,
                        )

                elif action == "delete":
                    if not record_id:
                        console.print("[red]✗ missing 'id' field[/red]")
                        results.append({"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "error", "error": "missing id"})
                        if ledger is not None and ledger_step_id is not None:
                            ledger.write_outcome(
                                step_id=ledger_step_id, status="failed",
                                bc_correlation_id=None,
                                error_message="missing id",
                                rollback_url=None,
                            )
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

                    if ledger is not None and ledger_step_id is not None:
                        ledger.write_outcome(
                            step_id=ledger_step_id, status="committed",
                            bc_correlation_id=None, error_message=None,
                            rollback_url=None,  # DELETE has no clean inverse
                        )

                else:
                    console.print(f"[yellow]? unknown action '{action}'[/yellow]")
                    results.append({"step": i, "name": step_name, "status": "skipped"})
                    if ledger is not None and ledger_step_id is not None:
                        ledger.write_outcome(
                            step_id=ledger_step_id, status="skipped",
                            bc_correlation_id=None,
                            error_message=f"unknown action '{action}'",
                            rollback_url=None,
                        )

            except Exception as e:
                console.print(f"[red]✗ {e}[/red]")
                results.append({"step": i, "name": step_name, "action": action, "endpoint": endpoint, "status": "error", "error": str(e)})
                if isinstance(context, WorkflowContext):
                    context.set_result(
                        step_name,
                        StepResult(name=step_name, action=action, endpoint=endpoint, status="error", error=str(e)),
                    )
                if ledger is not None and ledger_step_id is not None:
                    ledger.write_outcome(
                        step_id=ledger_step_id, status="failed",
                        bc_correlation_id=None,
                        error_message=str(e),
                        rollback_url=None,
                    )

    return results


# ─── Command: batch state ────────────────────────────────────────────


@app.command("state")
def state_cmd(
    run_id: str = typer.Argument(help="Run id (from `bcli batch list` or the run output)"),
    format: str = typer.Option("table", "--format", "-f", help="Output format: table | json"),
) -> None:
    """Show the ledger state for a single batch run."""
    ledger = Ledger(run_id=run_id)
    if not ledger.db_path.exists():
        console.print(f"[red]No ledger for run-id '{run_id}'.[/red]")
        raise typer.Exit(1)

    run = ledger.get_run(run_id)
    run["state"] = ledger.compute_run_state(run_id)  # truthful state
    steps = ledger.get_steps(run_id)
    ledger.close()

    if format == "json":
        out = {"run": run, "steps": steps}
        typer.echo(json.dumps(out, indent=2, default=str))
        return

    # Table layout
    console.print(f"[bold]Run:[/bold] {run['run_id']}")
    console.print(
        f"  [dim]Manifest:[/dim] {run['manifest_path']}\n"
        f"  [dim]Profile:[/dim]  {run['profile']} ({run['environment']} / {run['company']})\n"
        f"  [dim]Started:[/dim]  {run['started_at']}\n"
        f"  [dim]Finished:[/dim] {run.get('finished_at') or '(running)'}\n"
        f"  [dim]State:[/dim]    {run['state']}\n"
    )
    table = Table(title=f"Steps ({len(steps)})")
    table.add_column("seq", justify="right")
    table.add_column("method")
    table.add_column("url", overflow="fold")
    table.add_column("status")
    table.add_column("intent_ts")
    table.add_column("outcome_ts")
    for s in steps:
        table.add_row(
            str(s["seq"]), s["method"], s["url"] or "",
            s["status"] or "(none)", s["intent_ts"] or "",
            s["outcome_ts"] or "",
        )
    console.print(table)


# ─── Command: batch list ─────────────────────────────────────────────


@app.command("list")
def list_cmd(
    state_filter: str | None = typer.Option(
        None, "--state", help="Filter by derived run state (completed/failed/...)",
    ),
    limit: int = typer.Option(50, "--limit", help="Max rows to return"),
    format: str = typer.Option("table", "--format", "-f", help="Output format: table | json"),
) -> None:
    """List recent batch runs from the local ledger."""
    rows = Ledger.list_runs(state=state_filter, limit=limit)

    if format == "json":
        typer.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        console.print("[dim]No batch runs found.[/dim]")
        return

    table = Table(title="Batch runs")
    table.add_column("run_id")
    table.add_column("started_at")
    table.add_column("state")
    table.add_column("steps", justify="right")
    table.add_column("profile")
    table.add_column("manifest", overflow="fold")
    for r in rows:
        table.add_row(
            r["run_id"], r["started_at"] or "", r["state"] or "",
            str(r["step_count"]), r["profile"] or "",
            r["manifest_path"] or "",
        )
    console.print(table)


# ─── Command: batch rollback ─────────────────────────────────────────


@app.command(
    "rollback",
    help=(
        "Roll back a batch run.\n\n"
        "v0.1 limitation: only committed POSTs are reversed (DELETE on the "
        "captured record URL). PATCH and DELETE require manual cleanup — "
        "there is no clean inverse without a pre-image snapshot. Those "
        "steps are flagged 'rollback_skipped'.\n\n"
        "Refuses to run on profiles with disable_writes=true. The --yes "
        "flag does NOT bypass that gate."
    ),
)
def rollback_cmd(
    run_id: str = typer.Argument(help="Run id to roll back"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show which steps would be reversed; touch nothing.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the interactive confirmation prompt.",
    ),
) -> None:
    ledger = Ledger(run_id=run_id)
    if not ledger.db_path.exists():
        console.print(f"[red]No ledger for run-id '{run_id}'.[/red]")
        raise typer.Exit(1)

    # disable_writes is a HARD refusal here. The operator opted into
    # read-only mode — silently undoing committed work would defeat the
    # safety. --yes does NOT bypass this gate (unlike `batch run`).
    if getattr(state.profile, "disable_writes", False):
        console.print(
            f"[red]✗ Profile '{state.active_profile_name}' has "
            "disable_writes=true.[/red]"
        )
        console.print(
            "[dim]Rollback issues inverse mutations and is refused on "
            "read-only profiles. Switch to a writable profile to undo "
            "this run.[/dim]"
        )
        raise typer.Exit(1)

    steps = ledger.get_steps(run_id)
    # Reverse seq so we undo most recent first.
    steps_reverse = sorted(steps, key=lambda s: s["seq"], reverse=True)

    plan: list[tuple[dict, str]] = []  # (step_row, action)
    for s in steps_reverse:
        status = s["status"]
        method = (s["method"] or "").upper()
        if status != "committed":
            # Intent-only or already-failed/rolled steps are not eligible.
            continue
        if method == "POST" and s["rollback_url"]:
            plan.append((s, "delete"))
        elif method in {"PATCH", "DELETE"}:
            plan.append((s, "skip"))
        elif method == "POST" and not s["rollback_url"]:
            # POST that returned no id — we can't compose a DELETE URL.
            plan.append((s, "skip"))

    if not plan:
        console.print("[yellow]Nothing to roll back.[/yellow]")
        ledger.close()
        return

    # Summary preview before any mutation.
    console.print(
        f"[bold]Rollback plan for run {run_id}:[/bold] "
        f"{sum(1 for _, a in plan if a == 'delete')} delete(s), "
        f"{sum(1 for _, a in plan if a == 'skip')} skipped"
    )
    for step_row, plan_action in plan:
        verb = "DELETE" if plan_action == "delete" else "SKIP  "
        url = step_row["rollback_url"] or step_row["url"]
        console.print(f"  {verb}  {url}")

    if dry_run:
        ledger.close()
        return

    # Confirmation prompt. --yes skips.
    if not yes:
        import sys

        if not sys.stdin.isatty():
            console.print(
                "[red]✗ Refusing to roll back: non-interactive session and "
                "--yes was not passed.[/red]"
            )
            raise typer.Exit(1)
        answer = typer.prompt(
            "Type 'yes' to apply the rollback, anything else to cancel",
            default="",
            show_default=False,
        )
        if answer.strip().lower() != "yes":
            console.print("[red]✗ Cancelled.[/red]")
            raise typer.Exit(1)

    asyncio.run(_apply_rollback(plan, ledger=ledger))

    ledger.finish_run(run_id, "rolled_back")
    ledger.close()
    console.print(f"[green]✓[/green] Rollback applied for run {run_id}.")


async def _apply_rollback(plan: list[tuple[dict, str]], *, ledger: Ledger) -> None:
    """Issue the planned inverse operations and update step statuses."""
    async with state.make_async_client() as client:
        for step_row, plan_action in plan:
            if plan_action == "delete":
                url = step_row["rollback_url"]
                try:
                    await client.delete_url(url)
                    ledger.update_step_status(step_row["step_id"], "rolled_back")
                    console.print(f"  [green]✓[/green] DELETE {url}")
                except Exception as e:  # noqa: BLE001
                    ledger.update_step_status(step_row["step_id"], "rollback_failed")
                    console.print(f"  [red]✗[/red] DELETE {url}: {e}")
            else:
                ledger.update_step_status(step_row["step_id"], "rollback_skipped")
                console.print(
                    f"  [yellow]?[/yellow] SKIP {step_row['method']} "
                    f"{step_row['url']} (no clean inverse; manual cleanup required)"
                )
