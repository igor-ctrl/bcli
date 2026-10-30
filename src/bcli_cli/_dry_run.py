"""Shared ``--dry-run`` renderer for write commands.

One shape, every write command. ``post`` / ``patch`` / ``delete`` / ``action``
and ``batch run`` preview a ``WritePlan`` (see ``_write_cmd.render_plan``);
``attach upload`` / ``attach test`` build theirs with :func:`render_dry_run`.
Both end in :func:`render_preview`, so an agent driving any of them gets the
same machine-readable preview.

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
from bcli_cli._url_resolve import try_resolve_url

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
    force_standard: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    """Print a structured dry-run preview and ``typer.Exit(0)``.

    ``force_standard=True`` mirrors ``attach upload --standard``: the
    resolved URL skips the custom registry and points at the standard
    ``/api/v2.0/`` route, matching what the actual write would hit.
    """
    profile = state.profile

    # strict=True: a preview must not report success for a request the real
    # command would refuse. An unresolvable endpoint still previews with a null
    # URL (that is the documented behaviour); invalid input is fatal, so
    # `--dry-run` fails exactly where the real run fails.
    #
    # Presented here rather than propagated, for two reasons: the dry-run branch
    # in each write command sits above that command's own try/except, so a raw
    # raise surfaces as a traceback instead of the `Error: ...` line the real run
    # prints; and doing it in one place keeps post/patch/delete/attach/batch
    # consistent without five edits.
    try:
        resolved_url = try_resolve_url(
            endpoint,
            record_id=record_id,
            publisher=publisher,
            group=group,
            version=version,
            force_standard=force_standard,
            strict=True,
        )
    except ValueError as exc:
        _console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    payload: dict[str, Any] = {
        "dry_run": True,
        "method": method.upper(),
        "endpoint": endpoint,
        "resolved_url": resolved_url,
        "environment": profile.environment,
        "company_id": profile.company_id,
    }
    if record_id is not None:
        payload["record_id"] = record_id
    if body is not None:
        payload["body"] = body
    if extra:
        payload.update(extra)

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

    render_preview(payload)


def render_preview(payload: dict[str, Any]) -> None:
    """Print a dry-run ``payload`` in the active format and ``typer.Exit(0)``.

    Adds the active profile name; the caller is responsible for auditing.
    """
    keys = list(payload)
    at = keys.index("resolved_url") + 1 if "resolved_url" in keys else len(keys)
    items = list(payload.items())
    items.insert(at, ("profile", state.active_profile_name))
    payload = dict(items)

    if state.format in _MACHINE_FORMATS:
        indent = 2 if state.format == "json" else None
        print(json.dumps(payload, indent=indent, default=str))
    else:
        _render_human(payload)

    raise typer.Exit()


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
