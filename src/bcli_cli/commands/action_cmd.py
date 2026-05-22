"""bcli action — invoke an OData v4 bound action.

OData v4 *actions* are POST requests against a URL of the form

    <entitySet>(<key>)/<Namespace>.<actionName>

where ``<Namespace>.<actionName>`` is a server-side declared action
bound to the record at ``<entitySet>(<key>)``. Hand-writing that string
is error-prone (the parentheses + dot escaping in particular trips up
shells), so this verb composes the URL from named arguments and
forwards to the same client.post path that ``bcli post`` uses.

Spec-conformance notes:

* Actions are always POST regardless of any caller intent — OData v4
  does not allow GET on an action invocation.
* The default namespace is ``Microsoft.NAV`` (the BC convention); the
  ``--namespace`` flag overrides it for non-BC tenants. The registry
  validator itself is namespace-agnostic — see
  ``bcli.client._async._parse_bound_action``.
* ``--data`` defaults to an empty body when omitted (matching
  ``bcli post`` semantics). ``--no-data`` is retained as an explicit
  no-op alias for callers who want to spell the intent out; passing
  both ``--data`` and ``--no-data`` is still an error.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bcli_cli._envelope_wrap import capture, validate_flags
from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

console = Console(stderr=True)


DEFAULT_NAMESPACE = "Microsoft.NAV"


def action_command(
    entity_set: str = typer.Argument(
        ..., help="Parent entity set name (e.g. 'examples')",
    ),
    key: str = typer.Argument(
        ...,
        help=(
            "Record key inside the parens. Pass a GUID/int as bare text "
            "or a string key with explicit single quotes: \"'ALFKI'\"."
        ),
    ),
    action_name: str = typer.Argument(
        ..., help="Action identifier (e.g. 'archive')",
    ),
    data: Optional[str] = typer.Option(
        None,
        "--data",
        "-d",
        help="JSON body for the action (literal or @filename).",
    ),
    no_data: bool = typer.Option(
        False,
        "--no-data",
        help="Explicitly send an empty body. Same as omitting --data; "
             "kept for callers who want to spell the intent out.",
    ),
    namespace: Optional[str] = typer.Option(
        None,
        "--namespace",
        "-n",
        help=f"Action namespace (default: {DEFAULT_NAMESPACE}).",
    ),
    format: Optional[str] = typer.Option(
        None, "--format", "-f",
        help="Output format: table, json, csv, ndjson, raw",
    ),
    publisher: Optional[str] = typer.Option(None, "--publisher", hidden=True),
    group: Optional[str] = typer.Option(None, "--group", hidden=True),
    version: Optional[str] = typer.Option(None, "--version", hidden=True),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip the read-only-profile warning prompt",
    ),
    result_out: Optional[Path] = typer.Option(
        None, "--result-out",
        help="Write a JSON result envelope to this path (atomic). See AIP §Phase 2.",
    ),
    result_fd: Optional[int] = typer.Option(
        None, "--result-fd",
        help="Write the JSON result envelope to this file descriptor and close it.",
    ),
    idempotency_key: Optional[str] = typer.Option(
        None, "--idempotency-key",
        help="Opaque token forwarded as Idempotency-Key header (AIP §Phase 4d).",
    ),
) -> None:
    """Invoke an OData v4 bound action on a record.

    \b
    Examples:
        bcli action examples 42 archive
        bcli action items "'ALFKI'" doSomething --data '{"flag": true}'
        bcli action widgets 7 cancel --namespace Custom.Ns --data @payload.json
    """
    validate_flags(result_out, result_fd)

    # ``--data`` and ``--no-data`` may not both be set. Either alone, or
    # neither (defaults to ``{}``), is fine — matches ``bcli post``.
    if data is not None and no_data:
        raise typer.BadParameter(
            "--data and --no-data are mutually exclusive. "
            "Pass one or the other (or neither — empty body is the default).",
        )

    body: dict = {} if data is None else _parse_data(data)

    ns = namespace or DEFAULT_NAMESPACE
    # Compose the synthetic bound-action string. The registry validator
    # downstream (``_parse_bound_action``) will split it back into
    # parent + key + qualified-action; we don't try to second-guess that
    # parse here — keeping the bound-action shape the single source of
    # truth.
    composed = f"{entity_set}({key})/{ns}.{action_name}"

    output_format = format or state.format
    state.format = output_format
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    with capture(
        method="POST",
        endpoint=composed,
        result_out=result_out,
        result_fd=result_fd,
    ) as cap:
        from bcli_cli._safety import confirm_write_or_exit
        from bcli_cli._url_resolve import try_resolve_url

        cap.set_resolved_url(try_resolve_url(
            composed,
            publisher=publisher, group=group, version=version,
        ))

        # disable_writes / read-only-profile gate. Same policy as ``post``
        # — actions are mutating writes from the agent's perspective.
        confirm_write_or_exit("ACTION", composed, yes=yes)

        if state.dry_run:
            from bcli_cli._dry_run import render_dry_run
            cap.mark_dry_run()
            cap.emit_success()
            render_dry_run(
                "POST", composed, body=body,
                publisher=publisher, group=group, version=version,
            )

        try:
            result = asyncio.run(_audited_post(
                composed, body,
                publisher=publisher, group=group, version=version,
                idempotency_key=idempotency_key,
            ))
            cap.extract_record_id_from(result)
            cap.emit_success()
            if result:
                format_output([result], output_format)
            else:
                # 204 No Content — common for actions that mutate but
                # don't return a payload. Stay quiet on machine-readable
                # formats, print a friendly line on table/markdown.
                if output_format in (None, "table", "markdown"):
                    console.print("[green]✓[/green] Action invoked (204 No Content)")
        except Exception as e:
            cap.emit_failure(e)
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)


async def _audited_post(endpoint, body, **kwargs):
    from bcli_cli._audit_wrap import audited_write
    from bcli_cli._url_resolve import try_resolve_url
    resolved_url = try_resolve_url(
        endpoint,
        publisher=kwargs.get("publisher"),
        group=kwargs.get("group"),
        version=kwargs.get("version"),
    )
    return await audited_write(
        _execute_post(endpoint, body, **kwargs),
        method="POST", endpoint=endpoint, body=body,
        resolved_url=resolved_url,
    )


async def _execute_post(endpoint, body, **kwargs):
    async with state.make_async_client() as client:
        return await client.post(endpoint, body, **kwargs)


def _parse_data(data: str) -> dict:
    """Parse --data argument: JSON string or @filename."""
    if data.startswith("@"):
        path = Path(data[1:])
        if not path.is_file():
            raise typer.BadParameter(f"File not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(data)
