"""The one write path behind ``bcli post / patch / delete / action``.

Each command parses its own arguments, then hands a single call to
:func:`run_write`, which owns the sequence every mutation shares:

plan (resolve once) → envelope URL → ``disable_writes`` gate → dry-run
preview *or* send → envelope with BC's correlation id.

Auditing happens inside the SDK's ``client.write`` / ``client.record_dry_run``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import typer
from rich.console import Console

from bcli.client import AsyncBCClient, WritePlan, WriteResult
from bcli_cli._envelope_wrap import capture
from bcli_cli._state import state

_console = Console(stderr=True)


def run_write(
    method: str,
    endpoint: str,
    *,
    body: dict[str, Any] | None = None,
    record_id: str | None = None,
    etag: str = "*",
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
    idempotency_key: str | None = None,
    yes: bool = False,
    result_out: Path | None = None,
    result_fd: int | None = None,
    gate_label: str | None = None,
    check: Callable[[WriteResult], None] | None = None,
) -> WriteResult:
    """Plan, gate, preview-or-send one write and return its result.

    ``check`` runs after the write and before the success envelope, so a
    result the caller cannot use (``action --out`` with no payload) is
    recorded as failed. Dry-run exits via ``typer.Exit(0)``; any error prints
    ``Error: …`` and exits 1 (the gate exits 8).
    """
    from bcli_cli._safety import confirm_write_or_exit

    with capture(
        method=method, endpoint=endpoint, result_out=result_out, result_fd=result_fd,
    ) as cap:
        client = state.make_async_client()
        try:
            plan = client.plan(
                method, endpoint, body=body, record_id=record_id, etag=etag,
                publisher=publisher, group=group, version=version,
                idempotency_key=idempotency_key,
            )
        except Exception as e:
            _fail(cap, e)
        cap.set_resolved_url(plan.url)
        cap.set_record_id(record_id)

        # Inside the capture block so a refused write still emits a failed
        # envelope. See PR #15 review.
        confirm_write_or_exit(gate_label or method, endpoint, yes=yes)

        if state.dry_run:
            cap.mark_dry_run()
            cap.emit_success()
            render_plan(client, plan)

        try:
            result = asyncio.run(_send(client, plan))
            if method == "POST":
                # A create learns its id from the response; PATCH / DELETE keep
                # the key the caller addressed (an API keyed on No. still
                # returns a different systemId).
                cap.extract_record_id_from(result.body)
            if check is not None:
                check(result)
        except Exception as e:
            _fail(cap, e)
        cap.emit_success(bc_correlation_id=result.correlation_id)
        return result


def render_plan(client: AsyncBCClient, plan: WritePlan, *, method: str | None = None,
                extra: dict[str, Any] | None = None) -> None:
    """Print ``plan``'s dry-run preview, audit it, and ``typer.Exit(0)``."""
    from bcli_cli._dry_run import render_preview

    payload = plan.preview()
    if method is not None:
        payload["method"] = method
    if extra:
        payload.update(extra)
    client.record_dry_run(plan, method=method)
    render_preview(payload)


async def _send(client: AsyncBCClient, plan: WritePlan) -> WriteResult:
    async with client:
        return await client.write(plan)


def _fail(cap: Any, e: Exception) -> None:
    cap.emit_failure(e)
    _console.print(f"[red]Error:[/red] {e}")
    raise typer.Exit(1) from e
