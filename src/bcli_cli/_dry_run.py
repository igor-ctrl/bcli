"""Shared ``--dry-run`` renderer for write commands.

One helper, one shape, every write command. Used by ``bcli post``,
``patch``, ``delete``, ``attach upload``, ``attach test``, and
``batch run`` so an agent driving any of those gets the same machine-
readable preview without each command rolling its own format.

Output format follows the active CLI ``--format``:

* ``json``   — pretty-printed JSON envelope on stdout.
* ``ndjson`` — single-line JSON envelope on stdout (pipeable).
* ``raw``    — single-line JSON envelope on stdout.
* anything human (``table`` / ``markdown`` / ``csv`` / unset) — yellow
  warning + key-value summary on stderr; the request body, if any, also
  printed on stderr so the human view is one block on stderr and stdout
  stays clean.

The helper always raises ``typer.Exit(0)``; dry-run is a clean short-
circuit, not an error.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console

from bcli_cli._state import state

_console = Console(stderr=True)

# Formats that mean "an agent or pipe is consuming us; emit JSON on stdout"
_MACHINE_FORMATS = frozenset({"json", "ndjson", "raw"})


def render_dry_run(
    method: str,
    endpoint: str,
    *,
    body: Any | None = None,
    record_id: str | None = None,
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Print a structured dry-run preview and ``typer.Exit(0)``."""
    profile = state.profile
    profile_name = state.active_profile_name

    resolved_url = _try_resolve_url(
        endpoint,
        record_id=record_id,
        publisher=publisher,
        group=group,
        version=version,
    )

    payload: dict[str, Any] = {
        "dry_run": True,
        "method": method.upper(),
        "endpoint": endpoint,
        "resolved_url": resolved_url,
        "profile": profile_name,
        "environment": profile.environment,
        "company_id": profile.company_id,
    }
    if record_id is not None:
        payload["record_id"] = record_id
    if body is not None:
        payload["body"] = body
    if extra:
        payload.update(extra)

    if state.format in _MACHINE_FORMATS:
        indent = 2 if state.format == "json" else None
        print(json.dumps(payload, indent=indent, default=str))
    else:
        _render_human(payload)

    # Record the dry-run in the audit log when the user has it enabled,
    # so a paper trail captures every "would have done X" intent in
    # addition to actual writes.
    from bcli_cli._audit_wrap import emit_dry_run_audit
    emit_dry_run_audit(
        method,
        endpoint,
        body=body,
        record_id=record_id,
        resolved_url=resolved_url,
    )

    raise typer.Exit()


def _try_resolve_url(
    endpoint: str,
    *,
    record_id: str | None,
    publisher: str | None,
    group: str | None,
    version: str | None,
) -> str | None:
    """Best-effort URL resolution. Failures are non-fatal — the dry-run
    still prints with ``resolved_url: null`` so the user can see (and
    correct) what they asked for."""
    try:
        client = state.make_async_client()
        return client._resolve_url(
            endpoint,
            record_id=record_id,
            publisher=publisher,
            group=group,
            version=version,
        )
    except Exception:
        return None


def _render_human(payload: dict[str, Any]) -> None:
    method = payload["method"]
    endpoint = payload["endpoint"]
    record_id = payload.get("record_id")
    target = f"{endpoint}({record_id})" if record_id else endpoint

    _console.print(f"[yellow]--dry-run: would {method} {target}[/yellow]")
    if payload.get("resolved_url"):
        _console.print(f"[dim]  URL:        {payload['resolved_url']}[/dim]")
    _console.print(f"[dim]  Profile:    {payload['profile']}[/dim]")
    _console.print(f"[dim]  Env:        {payload['environment']}[/dim]")
    if payload.get("company_id"):
        _console.print(f"[dim]  Company:    {payload['company_id']}[/dim]")
    for key in ("file_path", "byte_size", "parent_type"):
        if key in payload:
            _console.print(f"[dim]  {key.replace('_', ' ').title():12}{payload[key]}[/dim]")
    body = payload.get("body")
    if body is not None:
        _console.print(json.dumps(body, indent=2, default=str))
