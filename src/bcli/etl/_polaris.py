"""Register dlt filesystem-destination load packages in an Apache Polaris Iceberg catalog.

After a dlt pipeline writes parquet files to S3, call
:func:`register_load_with_polaris` to commit those files as a new snapshot in
the Polaris-managed Iceberg table. The files are registered in-place via
``pyiceberg.Table.add_files`` — no data is rewritten.

This module is optional. It requires the ``pyiceberg`` extra::

    pip install 'bcli[polaris]'

The module lives in the ETL layer but imports bcli-agnostic libraries only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolarisConfig:
    """Connection settings for an Apache Polaris REST catalog."""

    uri: str
    warehouse: str
    credential: str
    namespace: str = "bc_raw"
    scope: str = "PRINCIPAL_ROLE:ALL"


def register_load_with_polaris(
    load_info: Any,
    *,
    pipeline: Any,
    config: PolarisConfig,
    entity_table_names: set[str] | None = None,
) -> dict[str, int]:
    """Register parquet files from the last dlt load as a Polaris snapshot.

    Args:
        load_info: The ``LoadInfo`` returned by ``pipeline.run(...)``.
        pipeline: The dlt pipeline instance (used to resolve destination paths).
        config: Polaris REST catalog connection settings.
        entity_table_names: Restrict to these destination table names. ``None``
            processes every user table in the load package (``_dlt*`` internal
            tables are always skipped).

    Returns:
        Mapping ``{table_name: files_added}`` — empty dict if the load had
        no parquet jobs.

    Raises:
        ImportError: if ``pyiceberg`` is not installed.
        RuntimeError: if a non-parquet job is seen for a user table.
    """
    try:
        from pyiceberg.catalog import load_catalog
    except ImportError as e:
        raise ImportError(
            "pyiceberg is required for Polaris integration. "
            "Install: pip install 'bcli[polaris]'"
        ) from e

    catalog = load_catalog(
        "polaris",
        type="rest",
        uri=config.uri,
        credential=config.credential,
        warehouse=config.warehouse,
        scope=config.scope,
    )

    client = pipeline.destination_client()
    bucket_url = client.config.bucket_url.rstrip("/")
    dataset = pipeline.dataset_name

    result: dict[str, int] = {}
    for package in load_info.load_packages:
        load_id = package.load_id
        jobs = package.jobs.get("completed_jobs", [])
        files_by_table: dict[str, list[str]] = {}
        for job in jobs:
            parsed = job.job_file_info
            table_name = parsed.table_name
            if table_name.startswith("_dlt"):
                continue
            if entity_table_names is not None and table_name not in entity_table_names:
                continue
            if parsed.file_format != "parquet":
                raise RuntimeError(
                    f"Polaris registration requires parquet files; got "
                    f"{parsed.file_format!r} for table {table_name!r}. "
                    f"Set loader_file_format='parquet'."
                )
            # dlt filesystem default layout: {table_name}/{load_id}.{file_id}.{ext}
            remote_file = f"{table_name}/{load_id}.{parsed.file_id}.{parsed.file_format}"
            remote_path = f"{bucket_url}/{dataset}/{remote_file}"
            files_by_table.setdefault(table_name, []).append(remote_path)

        for table_name, paths in files_by_table.items():
            table_ident = f"{config.namespace}.{table_name}"
            tbl = catalog.load_table(table_ident)
            added = _append_parquet_files_to_iceberg(tbl, paths, client=client)
            result[table_name] = result.get(table_name, 0) + added

    return result


def _append_parquet_files_to_iceberg(iceberg_table, parquet_uris, *, client) -> int:
    """Read parquet files via S3, cast to the Iceberg schema, and append.

    Using ``append`` (not ``add_files``) lets us absorb the schema deltas dlt
    produces — ISO-string timestamps that Iceberg stores as ``timestamptz``,
    and ``required`` vs ``optional`` column flags.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyarrow.compute as pc
    from pyiceberg.io.pyarrow import schema_to_pyarrow

    fs = _build_pyarrow_s3(client)
    target_schema = schema_to_pyarrow(iceberg_table.schema())

    # Iceberg stores timestamptz as microsecond precision with UTC.
    timestamp_target = pa.timestamp("us", tz="UTC")

    appended = 0
    for uri in parquet_uris:
        # Strip s3:// prefix for pyarrow.fs (which takes bucket/key form).
        key = uri[len("s3://"):] if uri.startswith("s3://") else uri
        arrow_tbl = pq.read_table(key, filesystem=fs)

        # Pre-cast any string column whose Iceberg counterpart is timestamp.
        for field in target_schema:
            if field.name not in arrow_tbl.column_names:
                continue
            src = arrow_tbl.schema.field(field.name)
            if pa.types.is_timestamp(field.type) and pa.types.is_string(src.type):
                new_col = pc.cast(arrow_tbl[field.name], timestamp_target)
                arrow_tbl = arrow_tbl.set_column(
                    arrow_tbl.column_names.index(field.name), field.name, new_col
                )

        # Align overall schema (nullability, column order) to Iceberg's.
        arrow_tbl = arrow_tbl.cast(target_schema, safe=False)

        iceberg_table.append(arrow_tbl)
        appended += 1

    return appended


def _build_pyarrow_s3(dlt_fs_client):
    """Construct a pyarrow S3FileSystem using credentials from the dlt client."""
    from pyarrow.fs import S3FileSystem

    creds = getattr(dlt_fs_client.config, "credentials", None)
    kwargs = {}
    if creds is not None:
        access_key = getattr(creds, "aws_access_key_id", None)
        secret_key = getattr(creds, "aws_secret_access_key", None)
        if access_key:
            kwargs["access_key"] = access_key
        if secret_key:
            kwargs["secret_key"] = secret_key
        region = getattr(creds, "region_name", None)
        if region:
            kwargs["region"] = region
    return S3FileSystem(**kwargs)
