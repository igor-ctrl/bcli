"""bcli acme — Acme-scoped workflows (two-phase /attachments upload)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bcli_cli._state import state
from bcli_cli.output import format_output, print_context_banner

console = Console(stderr=True)

app = typer.Typer(no_args_is_help=True, help="Acme-scoped workflows")


@app.command("attach")
def attach_command(
    file_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Path to the file to attach (PDF, etc.)"),
    parent_id: str = typer.Option(..., "--parent-id", help="Parent record systemId (e.g. purchase invoice id)"),
    parent_type: str = typer.Option("Purchase Invoice", "--parent-type", help="BC parent entity type"),
    file_name: Optional[str] = typer.Option(None, "--file-name", help="Override the attachment filename (defaults to the source filename)"),
    content_type: Optional[str] = typer.Option(None, "--content-type", help="Override Content-Type for the binary PATCH (defaults to mime-guess or application/octet-stream)"),
    publisher: Optional[str] = typer.Option(None, "--publisher", help="Custom API publisher (e.g. 'acme')"),
    group: Optional[str] = typer.Option(None, "--group", help="Custom API group (e.g. 'finance')"),
    version: Optional[str] = typer.Option(None, "--version", help="Custom API version (e.g. 'v1.5')"),
    standard: bool = typer.Option(False, "--standard", "--no-registry", help="Bypass the custom registry and force Microsoft's standard /api/v2.0/documentAttachments route. Use when a custom page isn't persisting (zero-GUID ids)."),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, json, csv, ndjson, raw"),
) -> None:
    """Upload a file as a documentAttachment linked to an existing parent record.

    Uses the two-phase ``documentAttachments`` pattern that lands bytes in
    table 1173 (the one the BC UI reads from):

    \b
      1. POST ``documentAttachments`` {parentType, parentId, fileName} → id + etag
      2. PATCH ``documentAttachments(<id>)/attachmentContent`` with raw bytes

    Routing follows the registry — custom entries for ``documentAttachments``
    take priority. Force a specific route with ``--publisher/--group/--version``.
    """
    output_format = format or state.format
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    if state.dry_run:
        console.print(
            f"[yellow]--dry-run: would upload {file_path} ({file_path.stat().st_size} bytes) "
            f"as attachment on {parent_type}({parent_id})[/yellow]"
        )
        raise typer.Exit()

    try:
        result = asyncio.run(
            _execute_attach(
                file_path=file_path,
                parent_type=parent_type,
                parent_id=parent_id,
                file_name=file_name,
                content_type=content_type,
                publisher=publisher,
                group=group,
                version=version,
                force_standard=standard,
            )
        )
        format_output([result] if result else [], output_format)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("test-attach")
def test_attach_command(
    file_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Path to the PDF to attach"),
    vendor_id: str = typer.Option(..., "--vendor-id", help="Vendor systemId for the new draft purchase invoice"),
    invoice_date: Optional[str] = typer.Option(None, "--invoice-date", help="Invoice date (YYYY-MM-DD); defaults to today"),
    file_name: Optional[str] = typer.Option(None, "--file-name", help="Override the attachment filename"),
    content_type: Optional[str] = typer.Option(None, "--content-type", help="Override Content-Type for the binary PATCH"),
    publisher: Optional[str] = typer.Option(None, "--publisher", help="Custom API publisher for the attach step"),
    group: Optional[str] = typer.Option(None, "--group", help="Custom API group for the attach step"),
    version: Optional[str] = typer.Option(None, "--version", help="Custom API version for the attach step"),
    standard: bool = typer.Option(False, "--standard", "--no-registry", help="Bypass the custom registry for the attach step (forces /api/v2.0/documentAttachments)"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, json, csv, ndjson, raw"),
) -> None:
    """End-to-end test: create a draft purchase invoice, then attach a file to it.

    Prints the new invoice number and the attachment id on success. Use this to
    verify both the purchaseInvoices endpoint AND the attachments two-phase
    upload in one shot.
    """
    output_format = format or state.format
    if output_format in ("json", "csv", "ndjson", "raw"):
        state.quiet = True

    print_context_banner()

    if state.dry_run:
        console.print(
            f"[yellow]--dry-run: would create draft purchaseInvoice for vendor {vendor_id}, "
            f"then attach {file_path}[/yellow]"
        )
        raise typer.Exit()

    try:
        result = asyncio.run(
            _execute_test_attach(
                file_path=file_path,
                vendor_id=vendor_id,
                invoice_date=invoice_date,
                file_name=file_name,
                content_type=content_type,
                publisher=publisher,
                group=group,
                version=version,
                force_standard=standard,
            )
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    invoice = result["invoice"]
    attachment = result["attachment"]

    if not state.quiet:
        console.print(
            f"[green]Created invoice[/green] [bold]{invoice.get('number', invoice.get('id'))}[/bold] "
            f"(systemId={invoice.get('id')})"
        )
        console.print(
            f"[green]Attached[/green] [bold]{attachment.get('fileName')}[/bold] "
            f"(id={attachment.get('id')}, {attachment.get('byteSize')} bytes)"
        )

    if output_format == "json":
        print(json.dumps(result, indent=2, default=str))
    elif output_format in ("ndjson", "raw"):
        print(json.dumps(result, default=str))


async def _execute_attach(
    *,
    file_path: Path,
    parent_type: str,
    parent_id: str,
    file_name: Optional[str],
    content_type: Optional[str],
    publisher: Optional[str],
    group: Optional[str],
    version: Optional[str],
    force_standard: bool = False,
) -> dict:
    async with state.make_async_client() as client:
        return await client.upload_attachment(
            parent_type=parent_type,
            parent_id=parent_id,
            file_path=file_path,
            file_name=file_name,
            content_type=content_type,
            publisher=publisher,
            group=group,
            version=version,
            force_standard=force_standard,
        )


async def _execute_test_attach(
    *,
    file_path: Path,
    vendor_id: str,
    invoice_date: Optional[str],
    file_name: Optional[str],
    content_type: Optional[str],
    publisher: Optional[str],
    group: Optional[str],
    version: Optional[str],
    force_standard: bool = False,
) -> dict:
    async with state.make_async_client() as client:
        invoice_body: dict = {"vendorId": vendor_id}
        if invoice_date:
            invoice_body["invoiceDate"] = invoice_date

        invoice = await client.post("purchaseInvoices", invoice_body)

        parent_id = invoice.get("id") or invoice.get("systemId")
        if not parent_id:
            raise RuntimeError(
                f"purchaseInvoices POST did not return an id. Response keys: {list(invoice)}"
            )

        attachment = await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id=parent_id,
            file_path=file_path,
            file_name=file_name,
            content_type=content_type,
            publisher=publisher,
            group=group,
            version=version,
            force_standard=force_standard,
        )
        return {"invoice": invoice, "attachment": attachment}
