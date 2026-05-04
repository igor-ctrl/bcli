"""Output formatters: table, markdown, json, csv, ndjson, raw."""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

# Belt-and-suspenders: if we *do* end up rendering rich's box-drawing chars
# on Windows, make sure they go out as UTF-8 bytes rather than the legacy
# CP1252-coded mojibake (`�`). Python ≥ 3.7 supports reconfigure(); bcli
# requires 3.10. No-op on POSIX.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # Detached or already-wrapped streams — leave them alone.
        pass

console = Console()
stderr_console = Console(stderr=True)


def detect_default_format() -> str:
    """Pick a sensible default format based on environment.

    AI coding agents (Claude Code, etc.) and piped/redirected stdout
    get a markdown table — readable, parseable, no ANSI escapes or
    box-drawing characters. Interactive TTYs get the rich table.

    On Windows, classic PowerShell pretends to be a TTY even when its
    stdout is being captured by a parent process (e.g. an AI agent's
    Bash tool), and renders rich's UTF-8 box-drawing as `�` mojibake on
    the default codepage. So we treat anything-but-Windows-Terminal as
    non-table by default. Set ``BCLI_FORMAT=table`` to force it.
    """
    if os.environ.get("BCLI_FORMAT"):
        return os.environ["BCLI_FORMAT"]
    # Claude Code sets CLAUDECODE=1; honor a generic agent override too.
    if os.environ.get("CLAUDECODE") or os.environ.get("BCLI_AGENT"):
        return "markdown"
    if not sys.stdout.isatty():
        return "markdown"
    if sys.platform == "win32" and not os.environ.get("WT_SESSION"):
        # Legacy console host (conhost.exe) — table rendering is unreliable.
        # Windows Terminal sets WT_SESSION; keep tables there.
        return "markdown"
    return "table"


def format_output(records: list[dict[str, Any]], fmt: str = "table") -> None:
    """Format and print records."""
    if not records:
        stderr_console.print("[dim]No records found.[/dim]")
        return

    formatter = _FORMATTERS.get(fmt, _format_table)
    formatter(records)


def _format_table(records: list[dict[str, Any]]) -> None:
    """Rich table output."""
    if not records:
        return

    # Pick columns from the first record, skip @odata metadata fields
    columns = [k for k in records[0].keys() if not k.startswith("@odata")]

    # Truncate wide tables
    max_cols = 12
    truncated = len(columns) > max_cols
    display_cols = columns[:max_cols]

    table = Table(show_header=True, header_style="bold", show_lines=False)
    # The first column is usually the identifier the caller actually needs
    # to read in full (`entity_set_name`, `no`, `systemId`, etc.). Letting
    # rich clip that to 40 chars + ellipsis turns useful output into
    # garbage on narrow terminals — the case that prompted this carve-out.
    # Subsequent columns stay capped so wide values don't push everything
    # off-screen.
    for i, col in enumerate(display_cols):
        if i == 0:
            table.add_column(col, no_wrap=True)
        else:
            table.add_column(col, overflow="ellipsis", max_width=40)
    if truncated:
        table.add_column("...", style="dim")

    for record in records:
        row = [_format_cell(record.get(col)) for col in display_cols]
        if truncated:
            row.append(f"+{len(columns) - max_cols} cols")
        table.add_row(*row)

    console.print(table)
    stderr_console.print(f"[dim]{len(records)} record(s)[/dim]")


def _format_markdown(records: list[dict[str, Any]]) -> None:
    """GitHub-flavored markdown table — agent-friendly default for non-TTYs."""
    if not records:
        return

    columns = [k for k in records[0].keys() if not k.startswith("@odata")]

    rows = [[_format_cell_markdown(r.get(c)) for c in columns] for r in records]

    # Column widths from headers + values, capped to keep lines reasonable
    max_width = 60
    widths = [len(c) for c in columns]
    for row in rows:
        for i, cell in enumerate(row):
            if len(cell) > widths[i]:
                widths[i] = min(len(cell), max_width)

    def _pad(cell: str, w: int) -> str:
        if len(cell) > w:
            return cell[: w - 1] + "…"
        return cell + " " * (w - len(cell))

    header = "| " + " | ".join(_pad(c, w) for c, w in zip(columns, widths)) + " |"
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    body = [
        "| " + " | ".join(_pad(cell, w) for cell, w in zip(row, widths)) + " |"
        for row in rows
    ]

    print(header)
    print(sep)
    for line in body:
        print(line)
    stderr_console.print(f"[dim]{len(records)} record(s)[/dim]")


def _format_json(records: list[dict[str, Any]]) -> None:
    """Pretty JSON output."""
    print(json.dumps(records, indent=2, default=str))


def _format_csv(records: list[dict[str, Any]]) -> None:
    """CSV output."""
    if not records:
        return
    columns = [k for k in records[0].keys() if not k.startswith("@odata")]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        writer.writerow({k: record.get(k, "") for k in columns})
    print(output.getvalue(), end="")


def _format_ndjson(records: list[dict[str, Any]]) -> None:
    """Newline-delimited JSON for pipelines."""
    for record in records:
        print(json.dumps(record, default=str))


def _format_raw(records: list[dict[str, Any]]) -> None:
    """Raw JSON including @odata metadata."""
    print(json.dumps({"value": records}, indent=2, default=str))


def _format_cell(value: Any) -> str:
    """Format a cell value for table display."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _format_cell_markdown(value: Any) -> str:
    """Format a cell value for markdown table display.

    Escapes pipes and collapses newlines so the row stays on one line.
    """
    cell = _format_cell(value)
    return cell.replace("|", "\\|").replace("\n", " ").replace("\r", "")


_FORMATTERS = {
    "table": _format_table,
    "markdown": _format_markdown,
    "md": _format_markdown,
    "json": _format_json,
    "csv": _format_csv,
    "ndjson": _format_ndjson,
    "raw": _format_raw,
}
