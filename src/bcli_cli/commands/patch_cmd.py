"""bcli patch — update records."""

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


def patch_command(
    endpoint: str = typer.Argument(help="Entity set name"),
    record_id: str = typer.Argument(help="Record ID to update"),
    data: str = typer.Option(..., "--data", "-d", help="JSON data or @filename"),
    etag: str = typer.Option("*", "--etag", help="ETag for optimistic concurrency"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, json, csv, ndjson, raw"),
    publisher: Optional[str] = typer.Option(None, "--publisher", help="Custom API publisher override (escape hatch — registry resolves this automatically)"),
    group: Optional[str] = typer.Option(None, "--group", help="Custom API group override (escape hatch — registry resolves this automatically)"),
    version: Optional[str] = typer.Option(None, "--version", help="Custom API version override (escape hatch — registry resolves this automatically)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the read-only-profile warning prompt"),
    result_out: Optional[Path] = typer.Option(
        None,
        "--result-out",
        help="Write a JSON result envelope to this path (atomic). See AIP §Phase 2.",
    ),
    result_fd: Optional[int] = typer.Option(
        None,
        "--result-fd",
        help="Write the JSON result envelope to this file descriptor and close it.",
    ),
    idempotency_key: Optional[str] = typer.Option(
        None,
        "--idempotency-key",
        help="Opaque token forwarded as Idempotency-Key header (AIP §Phase 4d).",
    ),
) -> None:
    """PATCH (update) an existing record."""
    validate_flags(result_out, result_fd)

    output_format = format or state.format
    state.format = output_format  # propagate subcommand -f to dry-run + audit
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    body = _parse_data(data)

    with capture(
        method="PATCH",
        endpoint=endpoint,
        result_out=result_out,
        result_fd=result_fd,
    ) as cap:
        from bcli_cli._safety import confirm_write_or_exit
        from bcli_cli._url_resolve import try_resolve_url

        cap.set_resolved_url(try_resolve_url(
            endpoint,
            record_id=record_id,
            publisher=publisher,
            group=group,
            version=version,
        ))
        cap.set_record_id(record_id)

        # Policy gate runs *inside* the capture block so a refused write
        # still emits a failed envelope. See PR #15 review.
        confirm_write_or_exit("PATCH", endpoint, yes=yes)

        if state.dry_run:
            from bcli_cli._dry_run import render_dry_run
            cap.mark_dry_run()
            cap.emit_success()
            render_dry_run(
                "PATCH", endpoint, body=body, record_id=record_id,
                publisher=publisher, group=group, version=version,
                extra={"etag": etag},
            )

        try:
            result = asyncio.run(_audited_patch(
                endpoint, record_id, body,
                etag=etag, publisher=publisher, group=group, version=version,
                idempotency_key=idempotency_key,
            ))
            cap.emit_success()
            format_output([result] if result else [], output_format)
        except Exception as e:
            cap.emit_failure(e)
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)


async def _audited_patch(endpoint, record_id, body, **kwargs):
    from bcli_cli._audit_wrap import audited_write
    from bcli_cli._url_resolve import try_resolve_url
    resolved_url = try_resolve_url(
        endpoint,
        record_id=record_id,
        publisher=kwargs.get("publisher"),
        group=kwargs.get("group"),
        version=kwargs.get("version"),
    )
    return await audited_write(
        _execute_patch(endpoint, record_id, body, **kwargs),
        method="PATCH", endpoint=endpoint, body=body, record_id=record_id,
        resolved_url=resolved_url,
    )


async def _execute_patch(endpoint, record_id, body, **kwargs):
    async with state.make_async_client() as client:
        return await client.patch(endpoint, record_id, body, **kwargs)


def _parse_data(data: str) -> dict:
    if data.startswith("@"):
        path = Path(data[1:])
        if not path.is_file():
            raise typer.BadParameter(f"File not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(data)
