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

from bcli.errors import BCLIError
from bcli_cli._envelope_wrap import capture, validate_flags
from bcli_cli._out_path import atomic_write_bytes, prepare_out_path
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
    out: Optional[Path] = typer.Option(
        None, "--out",
        help=(
            "Decode the action's base64 return value and write the raw bytes here. "
            "NOT the same as --result-out: --out writes the action's decoded payload "
            "bytes (a PDF, an export), --result-out writes the JSON result envelope "
            "describing the invocation."
        ),
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite",
        help="Replace an existing --out file (refused by default)",
    ),
    result_out: Optional[Path] = typer.Option(
        None, "--result-out",
        help=(
            "Write a JSON result envelope to this path (atomic). See AIP §Phase 2. "
            "For the action's payload bytes, see --out."
        ),
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
        bcli action documents 42 renderPdf --out document.pdf
    """
    validate_flags(result_out, result_fd)

    # Vet the destination before the write gate and before the POST. An action
    # can change BC; finding out afterwards that the payload has nowhere to go
    # would leave the mutation applied and the bytes lost.
    dest = prepare_out_path(out, overwrite=overwrite) if out is not None else None

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

            written: int | None = None
            if dest is not None:
                # Decode *before* emit_success: a return value we can't turn
                # into bytes means the caller didn't get what they asked for,
                # and the envelope has to say failed. The POST already
                # happened either way — that's what the envelope records.
                written = _write_decoded_payload(result, dest, overwrite=overwrite)

            cap.emit_success()

            if dest is not None:
                # Deliberately no format_output here: the payload is base64,
                # and dumping it to stdout after writing the decoded file is
                # noise at best and a wrecked pipe at worst.
                console.print(f"[green]✓[/green] Decoded {written:,} bytes to {dest}")
            elif result:
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


def _decode_base64_payload(result: dict | str) -> bytes:
    """Turn an action's return value into the raw bytes ``--out`` asked for.

    OData carries binary in an action's ``value`` property as base64, so that
    is the shape we decode. Anything else is reported rather than guessed at:
    an action that returned no payload, or a structured result, is a sign the
    caller wanted ``--result-out`` (or nothing at all), and writing a
    zero-byte file would look exactly like a successful download.
    """
    import base64
    import binascii

    if not result:
        raise BCLIError(
            "action returned 204 No Content — nothing to write to --out. The action "
            "ran; it just has no payload. Drop --out, or use --result-out to record "
            "the invocation itself."
        )

    if isinstance(result, str):
        payload = result
    elif isinstance(result.get("value"), str):
        payload = result["value"]
    else:
        keys = ", ".join(sorted(str(k) for k in result))
        raise BCLIError(
            f"action's return value has no base64 'value' property (keys: {keys}). "
            f"--out handles a base64 payload only — use --result-out for the JSON "
            f"result envelope, or drop --out to print the response."
        )

    try:
        return base64.b64decode(payload.strip(), validate=True)
    except (binascii.Error, ValueError) as e:
        preview = payload.strip()[:32]
        raise BCLIError(
            f"action's return value is not valid base64 (starts with: {preview!r}): {e}. "
            f"If you wanted the JSON response rather than a decoded payload, use "
            f"--result-out or drop --out."
        ) from e


def _write_decoded_payload(result: dict | str, dest: Path, *, overwrite: bool) -> int:
    """Decode the action's payload onto ``dest`` atomically; return byte count."""
    raw = _decode_base64_payload(result)
    atomic_write_bytes(dest, raw, overwrite=overwrite)
    return len(raw)


def _parse_data(data: str) -> dict:
    """Parse --data argument: JSON string or @filename."""
    if data.startswith("@"):
        path = Path(data[1:])
        if not path.is_file():
            raise typer.BadParameter(f"File not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(data)
