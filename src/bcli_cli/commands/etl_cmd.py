"""bcli etl — extract Business Central data via dlt pipelines."""

from __future__ import annotations

import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bcli_cli._state import state
from bcli_cli.output import print_context_banner

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("entities")
def list_entities(
    include_standard: bool = typer.Option(False, "--include-standard", help="Also show standard v2.0 entities"),
) -> None:
    """List Business Central entities available for ETL extraction.

    By default shows only custom API endpoints from the registry for the
    active profile. Use --include-standard to also show standard v2.0 entities.
    """
    from bcli.etl._bridge import load_entities_from_bcli_registry

    profile = state.profile_name or "default"
    entities = load_entities_from_bcli_registry(profile, custom_only=not include_standard)

    if not entities:
        console.print(f"[yellow]No custom endpoints found for profile '{profile}'.[/yellow]")
        console.print("[dim]Import endpoints first: bcli registry import --from-postman <file>[/dim]")
        raise typer.Exit()

    table = Table(title=f"ETL Entities — profile: {profile}")
    table.add_column("Entity", style="cyan")
    table.add_column("Primary Key", style="dim")
    table.add_column("Route", style="dim")
    table.add_column("Cursor", style="dim")

    for entity in entities:
        route = ""
        if entity.api_publisher:
            route = f"{entity.api_publisher}/{entity.api_group}/{entity.api_version}"
        else:
            route = "v2.0 (standard)"

        table.add_row(
            entity.name,
            entity.primary_key,
            route,
            entity.cursor_field,
        )

    console.print(table)
    console.print(f"\n[dim]{len(entities)} entities available[/dim]")


@app.command("sync")
def sync(
    entities: Optional[str] = typer.Option(None, "--entities", help="Comma-separated entity names (default: all custom)"),
    destination: str = typer.Option("filesystem", "--destination", "-d", help="dlt destination: filesystem, duckdb, iceberg"),
    dataset: str = typer.Option("bc_raw", "--dataset", help="Dataset name in destination"),
    pipeline_name: str = typer.Option("bcli_etl", "--pipeline", help="Pipeline name for state tracking"),
    full_refresh: bool = typer.Option(False, "--full-refresh", help="Ignore cursor, reload everything"),
    include_standard: bool = typer.Option(False, "--include-standard", help="Also sync standard v2.0 entities"),
    file_format: str = typer.Option("jsonl", "--file-format", help="Filesystem loader file format: jsonl or parquet"),
    polaris_uri: Optional[str] = typer.Option(None, "--polaris-uri", envvar="BCLI_POLARIS_URI", help="Polaris REST catalog URI. Enables post-sync Iceberg registration."),
    polaris_warehouse: Optional[str] = typer.Option(None, "--polaris-warehouse", envvar="BCLI_POLARIS_WAREHOUSE", help="Polaris catalog (warehouse) name"),
    polaris_credential: Optional[str] = typer.Option(None, "--polaris-credential", envvar="BCLI_POLARIS_CREDENTIAL", help="Polaris OAuth credential in 'client_id:client_secret' form"),
    polaris_namespace: str = typer.Option("bc_raw", "--polaris-namespace", envvar="BCLI_POLARIS_NAMESPACE", help="Iceberg namespace inside the Polaris warehouse"),
) -> None:
    """Extract Business Central data and load to a destination via dlt.

    By default syncs all custom API endpoints from the registry for the active
    profile. Standard v2.0 entities are skipped (typically handled by Fivetran).

    \b
    Examples:
        bcli etl sync --destination filesystem
        bcli etl sync --entities customers,vendors --destination duckdb
        bcli etl sync --full-refresh --destination iceberg
    """
    try:
        import dlt
    except ImportError:
        console.print("[red]dlt is required for ETL features.[/red]")
        console.print("[dim]Install it: pip install 'bc-cli[etl]'[/dim]")
        raise typer.Exit(1)

    print_context_banner()

    profile = state.profile_name or "default"
    entity_list = [e.strip() for e in entities.split(",")] if entities else None

    # Preview what will be synced
    from bcli.etl._bridge import load_entities_from_bcli_registry

    available = load_entities_from_bcli_registry(profile, custom_only=not include_standard)
    if not available:
        console.print(f"[yellow]No custom endpoints found for profile '{profile}'.[/yellow]")
        console.print("[dim]Import endpoints first: bcli registry import --from-postman <file>[/dim]")
        raise typer.Exit()

    sync_count = len(entity_list) if entity_list else len(available)

    polaris_enabled = bool(polaris_uri and polaris_warehouse and polaris_credential)
    if polaris_enabled and file_format != "parquet":
        file_format = "parquet"

    console.print("[bold]ETL Sync[/bold]")
    console.print(f"  Profile: {profile}")
    console.print(f"  Destination: {destination}")
    console.print(f"  Dataset: {dataset}")
    console.print(f"  Entities: {', '.join(entity_list) if entity_list else f'all ({sync_count} custom)'}")
    console.print(f"  Mode: {'full refresh' if full_refresh else 'incremental (systemModifiedAt)'}")
    console.print(f"  File format: {file_format}")
    if polaris_enabled:
        console.print(f"  Polaris: {polaris_uri} → {polaris_warehouse}.{polaris_namespace}")
    console.print()

    try:
        from bcli.etl import bcli_profile

        source = bcli_profile(
            profile=profile,
            entities=entity_list,
            full_refresh=full_refresh,
            include_standard=include_standard,
        )

        if destination == "filesystem":
            os.environ.setdefault(
                "DESTINATION__FILESYSTEM__LOADER_FILE_FORMAT", file_format
            )

        pipeline = dlt.pipeline(
            pipeline_name=pipeline_name,
            destination=destination,
            dataset_name=dataset,
        )

        console.print("[dim]Running pipeline...[/dim]")
        load_info = pipeline.run(source, loader_file_format=file_format) if destination == "filesystem" else pipeline.run(source)

        console.print("\n[green]✓[/green] Pipeline complete")
        console.print(f"[dim]{load_info}[/dim]")

        if polaris_enabled:
            _register_polaris(
                load_info=load_info,
                pipeline=pipeline,
                uri=polaris_uri,
                warehouse=polaris_warehouse,
                credential=polaris_credential,
                namespace=polaris_namespace,
                entity_list=entity_list,
                available=available,
            )

    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]ETL failed:[/red] {e}")
        raise typer.Exit(1)


def _register_polaris(
    *,
    load_info,
    pipeline,
    uri: str,
    warehouse: str,
    credential: str,
    namespace: str,
    entity_list: list[str] | None,
    available: list,
) -> None:
    """Register just-loaded parquet files in Polaris and print a summary."""
    from bcli.etl import PolarisConfig, register_load_with_polaris

    # Map selected entity names → destination table names dlt wrote.
    # dlt normalizes camelCase to snake_case (e.g. glEntries → gl_entries).
    from dlt.common.normalizers.naming.snake_case import NamingConvention

    naming = NamingConvention()
    selected = set(entity_list) if entity_list else {e.name for e in available}
    entity_table_names = {naming.normalize_identifier(name) for name in selected}

    config = PolarisConfig(
        uri=uri,
        warehouse=warehouse,
        credential=credential,
        namespace=namespace,
    )

    console.print(f"\n[dim]Registering load with Polaris ({uri})...[/dim]")
    try:
        summary = register_load_with_polaris(
            load_info,
            pipeline=pipeline,
            config=config,
            entity_table_names=entity_table_names,
        )
    except ImportError as e:
        console.print(f"[yellow]Polaris skipped:[/yellow] {e}")
        return

    if not summary:
        console.print("[dim]Polaris: nothing to register (no parquet files in load).[/dim]")
        return

    for table, count in sorted(summary.items()):
        console.print(f"[green]✓[/green] Polaris: {namespace}.{table} ← {count} file(s)")
