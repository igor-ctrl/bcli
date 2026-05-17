"""Output formatters: table, markdown, records, json, csv, ndjson, raw."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
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

    AIP §Phase 4b — when stdout isn't a TTY the consumer is programmatic
    (pipe, redirect, CI step, agent runtime). Emit JSON: the canonical
    machine-readable shape, no ANSI/box-drawing characters, no
    ambiguity. Interactive TTYs still get the rich table.

    Explicit user hints continue to win:

    * ``BCLI_FORMAT=<fmt>``           — pin any format.
    * ``CLAUDECODE`` / ``BCLI_AGENT`` — markdown (legacy AI-agent
      semantics; agents that prefer JSON now can just pass
      ``--format json`` or unset the env var).

    On Windows, classic PowerShell pretends to be a TTY even when
    captured. ``rich`` renders box-drawing as ``?`` mojibake there,
    so anything-but-Windows-Terminal stays on markdown. Setting
    ``BCLI_FORMAT=table`` forces tables if the user prefers.
    """
    if os.environ.get("BCLI_FORMAT"):
        return os.environ["BCLI_FORMAT"]
    # Claude Code sets CLAUDECODE=1; honor a generic agent override too.
    if os.environ.get("CLAUDECODE") or os.environ.get("BCLI_AGENT"):
        return "markdown"
    if not sys.stdout.isatty():
        # Phase 4b: pipes / redirects default to JSON for programmatic
        # consumers. Explicit ``--format`` on the CLI always wins.
        return "json"
    if sys.platform == "win32" and not os.environ.get("WT_SESSION"):
        # Legacy console host (conhost.exe) — table rendering is unreliable.
        # Windows Terminal sets WT_SESSION; keep tables there.
        return "markdown"
    return "table"


def format_output(
    records: list[dict[str, Any]],
    fmt: str = "table",
    *,
    auto_format: bool = True,
) -> None:
    """Format and print records.

    When ``auto_format`` is ``True`` (the default for code paths that
    chose the format via :func:`detect_default_format`), wide single-row
    results in ``table`` / ``markdown`` modes flip to vertical records
    view. A user who explicitly passed ``-f markdown`` or set
    ``BCLI_FORMAT=markdown`` gets exactly markdown — the contract they
    asked for is honored even when the output is too wide to render
    cleanly. Pass ``auto_format=False`` from CLI dispatchers when the
    user supplied ``-f`` directly.
    """
    if not records:
        stderr_console.print("[dim]No records found.[/dim]")
        return

    if auto_format and fmt in ("table", "markdown", "md") and _should_auto_records(records):
        fmt = "records"

    formatter = _FORMATTERS.get(fmt, _format_table)
    formatter(records)


def _should_auto_records(records: list[dict[str, Any]]) -> bool:
    """Decide whether to flip a wide table into a vertical record view.

    Triggers when:
      * the user has not pinned a format explicitly (BCLI_FORMAT respected
        upstream — see ``detect_default_format``),
      * the record set is small (1-2 rows), AND
      * either the column count exceeds 8 OR the rendered first-row width
        would exceed the terminal width.

    A small record count is the safety bound: vertical view scales badly
    past ~5 records and the user is better served by JSON/CSV at that point.
    """
    if os.environ.get("BCLI_NO_AUTO_RECORDS"):
        return False
    if len(records) > 2:
        return False
    columns = [k for k in records[0].keys() if not k.startswith("@odata")]
    if len(columns) <= 6:
        return False
    # Always flip when there are very many columns — terminal width can't
    # be trusted on Windows Terminal under some shells, and 8+ columns in
    # 1-2 rows is the canonical "give me the engine record" lookup shape.
    if len(columns) > 8:
        return True
    width = shutil.get_terminal_size((120, 24)).columns
    estimated = sum(
        max(len(c), len(_format_cell(records[0].get(c)))) + 3 for c in columns
    )
    return estimated > width


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


def _format_records(records: list[dict[str, Any]]) -> None:
    """Vertical "psql \\x"-style view: one field per line, blank line between rows.

    Output shape (line-wrapping aside, every field stays on its own line):

        record 1
          systemId           : b1fc5e63-…
          engineSerialNumber : 194108
          engineType         : CF34-8C
          …

        record 2
          …

    This is the right view for wide records on narrow terminals (Windows
    PowerShell with ~120 columns and an engine record with ~30 fields is the
    motivating case). It works in any terminal — no box-drawing, no ANSI,
    safe to redirect.
    """
    if not records:
        return

    columns = [k for k in records[0].keys() if not k.startswith("@odata")]
    if not columns:
        return
    label_width = min(40, max(len(c) for c in columns))

    multi = len(records) > 1
    for idx, record in enumerate(records, start=1):
        if multi:
            print(f"record {idx}")
        for col in columns:
            label = col.ljust(label_width)
            value = _format_cell(record.get(col))
            # A multi-line cell becomes "label : line1\n  <padding> | line2"
            # rather than re-printing the label, so the grouping stays visible.
            lines = value.splitlines() or [""]
            print(f"  {label} : {lines[0]}")
            for extra in lines[1:]:
                print(f"  {' ' * label_width}   {extra}")
        if idx != len(records):
            print()
    stderr_console.print(f"[dim]{len(records)} record(s)[/dim]")


_FORMATTERS = {
    "table": _format_table,
    "markdown": _format_markdown,
    "md": _format_markdown,
    "records": _format_records,
    "record": _format_records,
    "r": _format_records,
    "vertical": _format_records,
    "json": _format_json,
    "csv": _format_csv,
    "ndjson": _format_ndjson,
    "raw": _format_raw,
}
