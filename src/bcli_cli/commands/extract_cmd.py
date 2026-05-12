"""bcli extract — PDF → batch.yaml via a pluggable AI extraction backend.

Pipeline:

    PDF + YAML schema → backend.extract() → ExtractionResult
        → <pdf>.batch.yaml             (workflow the operator runs)
        → <pdf>.extracted.json         (traceability sidecar)

The command does NOT post to BC. It produces files for a human reviewer
to validate before ``bcli batch run`` mutates anything. The intended
landing sequence is in the batch.yaml's header comment and in
``docs/extraction.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bcli.errors import ExtractError
from bcli.extract import get_extractor, load_schema
from bcli.extract._schema import discover_schemas
from bcli.extract._yaml_writer import render_batch_yaml, render_sidecar_json
from bcli_cli._state import state

app = typer.Typer(no_args_is_help=True, help="PDF → batch.yaml extraction (AI vision)")
console = Console(stderr=True)


@app.command("run")
def run_command(
    pdf_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True, help="Path to the source PDF."
    ),
    schema: str = typer.Option(
        ..., "--schema", "-s",
        help="Schema slug (filename without .yaml) or absolute path to a schema YAML.",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Output batch.yaml path. Default: <pdf-stem>.batch.yaml next to the PDF.",
    ),
    sidecar: Optional[Path] = typer.Option(
        None, "--sidecar",
        help="Sidecar JSON path. Default: <pdf-stem>.extracted.json next to the PDF.",
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite",
        help="Overwrite existing output files instead of erroring.",
    ),
) -> None:
    """Extract structured records from a PDF and emit batch.yaml + sidecar.

    The generated batch.yaml is intentionally NOT run automatically.
    Review the sidecar against the PDF, then:

    \b
        bcli batch run <output>.batch.yaml --profile sandbox --dry-run
        bcli batch run <output>.batch.yaml --profile sandbox
        # verify in BC sandbox UI, then:
        bcli batch run <output>.batch.yaml --profile production
    """
    cfg = state.config
    schema_path = _resolve_schema_path(schema, cfg.extract.schemas_dir)
    schema_obj = load_schema(schema_path)

    out_yaml = output or pdf_path.with_suffix(".batch.yaml")
    out_json = sidecar or pdf_path.with_suffix(".extracted.json")
    _check_writeable(out_yaml, overwrite=overwrite)
    _check_writeable(out_json, overwrite=overwrite)

    extractor = get_extractor(cfg.extract)
    if not extractor.is_active:
        console.print(
            "[yellow]No extraction backend is active.[/yellow] "
            "Set [bold]\\[extract] backend = \"claude\"[/bold] in "
            "~/.config/bcli/config.toml and install [bold]bc-cli[extract][/bold]."
        )
        raise typer.Exit(2)

    console.print(
        f"[dim]Extracting[/dim] [bold]{schema_obj.name}[/bold] from "
        f"[bold]{pdf_path.name}[/bold] via [bold]{cfg.extract.backend}[/bold] "
        f"([dim]{cfg.extract.model}[/dim])"
    )

    try:
        result = extractor.extract(pdf_path, schema_obj)
    except ExtractError as e:
        console.print(f"[red]Extract failed:[/red] {e}")
        raise typer.Exit(1)

    if not result.records:
        joined = "; ".join(result.warnings) if result.warnings else "no warnings"
        console.print(
            f"[yellow]No records extracted.[/yellow] {joined}"
        )
        raise typer.Exit(1)

    out_yaml.write_text(
        render_batch_yaml(result, schema_obj, source_pdf=pdf_path), encoding="utf-8"
    )
    out_json.write_text(
        render_sidecar_json(result, schema_obj, source_pdf=pdf_path), encoding="utf-8"
    )

    console.print(
        f"[green]✓[/green] {len(result.records)} record(s) → "
        f"[bold]{out_yaml}[/bold]"
    )
    console.print(
        f"[green]✓[/green] traceability sidecar → [bold]{out_json}[/bold]"
    )
    if result.warnings:
        console.print(
            "[yellow]Warnings:[/yellow] " + "; ".join(result.warnings)
        )
    console.print(
        "\n[dim]Next:[/dim]\n"
        f"  1. Eyeball the sidecar against {pdf_path.name}\n"
        f"  2. [bold]bcli batch run {out_yaml} --profile sandbox --dry-run[/bold]\n"
        f"  3. [bold]bcli batch run {out_yaml} --profile sandbox[/bold]\n"
        f"  4. Verify in the BC sandbox UI\n"
        f"  5. [bold]bcli batch run {out_yaml} --profile production[/bold]\n"
    )


@app.command("list-schemas")
def list_schemas_command() -> None:
    """Show extraction schemas discovered for the active profile."""
    schemas_dir = _resolve_schemas_dir(state.config.extract.schemas_dir)
    schemas = discover_schemas(schemas_dir)

    table = Table(title=f"Extraction schemas — {schemas_dir}")
    table.add_column("Slug", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Endpoint", style="dim")
    table.add_column("List?", style="dim")

    if not schemas:
        console.print(
            f"[yellow]No schemas found in[/yellow] {schemas_dir}.\n"
            f"Drop a [bold]<slug>.yaml[/bold] file in that directory or pass "
            "[bold]--schema /absolute/path/to/schema.yaml[/bold]."
        )
        return

    for slug, path in schemas.items():
        try:
            s = load_schema(path)
            table.add_row(
                slug, s.name, s.output.endpoint, "yes" if s.list else "no"
            )
        except ExtractError as e:
            table.add_row(slug, f"[red]invalid: {e}[/red]", "-", "-")

    console.print(table)


def _resolve_schema_path(schema: str, schemas_dir_cfg: str | None) -> Path:
    candidate = Path(schema)
    if candidate.suffix.lower() in (".yaml", ".yml") and candidate.is_file():
        return candidate

    schemas_dir = _resolve_schemas_dir(schemas_dir_cfg)
    schemas = discover_schemas(schemas_dir)
    if schema in schemas:
        return schemas[schema]

    available = ", ".join(sorted(schemas.keys())) if schemas else "(none)"
    raise typer.BadParameter(
        f"Schema '{schema}' not found. Looked for a file at that path and "
        f"for a slug under {schemas_dir}. Available slugs: {available}."
    )


def _resolve_schemas_dir(configured: str | None) -> Path:
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "bcli" / "extract" / "schemas"


def _check_writeable(path: Path, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        console.print(
            f"[red]Refusing to overwrite[/red] {path} — pass [bold]--overwrite[/bold] "
            "to replace it."
        )
        raise typer.Exit(1)
