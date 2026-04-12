"""Output formatters: table, json, csv, ndjson, raw."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()


def format_output(records: list[dict[str, Any]], fmt: str = "table") -> None:
    """Format and print records."""
    if not records:
        console.print("[dim]No records found.[/dim]")
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
    for col in display_cols:
        table.add_column(col, overflow="ellipsis", max_width=40)
    if truncated:
        table.add_column("...", style="dim")

    for record in records:
        row = [_format_cell(record.get(col)) for col in display_cols]
        if truncated:
            row.append(f"+{len(columns) - max_cols} cols")
        table.add_row(*row)

    console.print(table)
    console.print(f"[dim]{len(records)} record(s)[/dim]")


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


_FORMATTERS = {
    "table": _format_table,
    "json": _format_json,
    "csv": _format_csv,
    "ndjson": _format_ndjson,
    "raw": _format_raw,
}
