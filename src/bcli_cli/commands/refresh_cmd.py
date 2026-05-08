"""``bcli config refresh`` and friends — team bundle pull / rollback / publish.

Implementation lives here; the commands are registered onto the existing
``bcli config`` Typer app at the bottom of :mod:`config_cmd` so the user
sees ``bcli config refresh``.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from bcli.bundle import (
    BundleApplyResult,
    BundleFetchError,
    BundleVerifyError,
    Sha256Verifier,
    apply_bundle,
    fetch_bundle,
    load_local_manifest,
    rollback_bundle,
    verify_bundle,
)
from bcli.bundle._publish import make_bundle
from bcli.config._defaults import CONFIG_DIR, REGISTRIES_DIR
from bcli_cli._state import state

logger = logging.getLogger("bcli.bundle.cli")
console = Console(stderr=True)
out = Console()

BUNDLES_DIR = CONFIG_DIR / "bundles"
QUERIES_DIR = CONFIG_DIR / "queries"


def _bundle_url_for_profile(profile_name: str) -> str | None:
    """Resolve the bundle URL for ``profile_name`` from config.

    The profile model carries arbitrary extras (``model_config = extra:
    "allow"``) so we look for ``bundle_url`` directly on the profile, and
    fall back to a top-level ``[bundles] <profile> = "..."`` table if
    present. Either form keeps admins out of code edits.
    """
    cfg = state.config
    profile = cfg.get_profile(profile_name)
    direct = getattr(profile, "bundle_url", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    extra = getattr(cfg, "model_extra", None) or {}
    bundles = extra.get("bundles") if isinstance(extra, dict) else None
    if isinstance(bundles, dict):
        url = bundles.get(profile_name)
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


# ─── refresh ──────────────────────────────────────────────────────────


def refresh_command(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p",
        help="Profile to refresh (default: active profile)",
    ),
    url: Optional[str] = typer.Option(
        None, "--url",
        help="Override the bundle URL for this run (otherwise read from config)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Fetch and verify the bundle but do not apply",
    ),
    skip_verify: bool = typer.Option(
        False, "--skip-verify",
        help="Skip checksum verification (dev-only — requires BCLI_DEV=1)",
    ),
) -> None:
    """Pull the latest team bundle for a profile and apply it atomically.

    \b
    Examples:
      bcli config refresh
      bcli config refresh --profile finance
      bcli config refresh --dry-run
      BCLI_DEV=1 bcli config refresh --url file:///tmp/finance-2026.05.07-1.tar.gz
    """
    import os

    dev_mode = os.environ.get("BCLI_DEV") in ("1", "true", "yes")
    if skip_verify and not dev_mode:
        console.print(
            "[red]--skip-verify is dev-only.[/red] Set BCLI_DEV=1 to opt in. "
            "Production refreshes must verify; refusing to bypass."
        )
        raise typer.Exit(1)

    profile_name = profile or state.active_profile_name
    resolved_url = url or _bundle_url_for_profile(profile_name)
    if not resolved_url:
        console.print(
            f"[red]No bundle URL configured for profile '{profile_name}'.[/red]\n"
            "[dim]Add `bundle_url = \"https://...\"` to the profile in "
            "~/.config/bcli/config.toml, or pass --url for a one-off pull.[/dim]"
        )
        raise typer.Exit(1)

    console.print(
        f"[bold]Refreshing[/bold] [cyan]{profile_name}[/cyan] from {resolved_url}"
    )

    try:
        bundle, raw = fetch_bundle(resolved_url)
    except BundleFetchError as e:
        console.print(f"[red]Fetch failed:[/red] {e}")
        raise typer.Exit(1) from e

    try:
        if not skip_verify:
            try:
                verify_bundle(bundle, verifier=Sha256Verifier(), raw_archive=raw)
            except BundleVerifyError as e:
                console.print(f"[red]Verify failed:[/red] {e}")
                raise typer.Exit(1) from e
            console.print("  [green]✓[/green] checksum verified")

        if bundle.manifest.profile != profile_name:
            console.print(
                f"[yellow]Warning:[/yellow] bundle declares profile "
                f"'{bundle.manifest.profile}' but you are refreshing "
                f"'{profile_name}'. Refusing to apply mismatched profiles."
            )
            raise typer.Exit(1)

        previous = load_local_manifest(BUNDLES_DIR, profile_name)
        console.print(
            f"  Current:  [dim]{previous.version if previous else '(none)'}[/dim]"
        )
        console.print(
            f"  Latest:   [bold]{bundle.manifest.version}[/bold] "
            f"[dim](published {bundle.manifest.published_at:%Y-%m-%d}"
            + (
                f" by {bundle.manifest.publisher}"
                if bundle.manifest.publisher else ""
            )
            + ")[/dim]"
        )
        if bundle.manifest.release_notes:
            console.print(f"  Notes:    {bundle.manifest.release_notes}")

        if dry_run:
            console.print("[yellow]--dry-run[/yellow] — verified but not applied.")
            return

        result = apply_bundle(
            bundle,
            registries_dir=REGISTRIES_DIR,
            queries_dir=QUERIES_DIR,
            bundle_dir=BUNDLES_DIR,
        )
        _print_apply_summary(result)
    finally:
        # Always clean up the temp extraction tree — covers verify
        # failure, profile mismatch, dry-run exit, and apply crashes.
        shutil.rmtree(bundle.root, ignore_errors=True)


def _print_apply_summary(result: BundleApplyResult) -> None:
    parts = []
    if result.registry_changed:
        parts.append("registry")
    if result.queries_changed:
        parts.append("queries")
    if result.field_lists_changed:
        parts.append("field lists")
    changed = ", ".join(parts) if parts else "no files changed"
    console.print(
        f"  [green]Applied[/green] {result.new_version} ({changed})."
    )
    if result.previous_version:
        console.print(
            f"  [dim]Previous version {result.previous_version} retained "
            "for `bcli config refresh --rollback`.[/dim]"
        )


# ─── rollback ─────────────────────────────────────────────────────────


def rollback_command(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p",
        help="Profile to roll back (default: active profile)",
    ),
) -> None:
    """Restore the previous bundle for a profile.

    Idempotent — safe to run twice. If no backup exists, the in-place
    manifest is wiped so ``bcli doctor`` reverts to "bundle not installed".
    """
    profile_name = profile or state.active_profile_name
    rolled = rollback_bundle(
        profile_name,
        registries_dir=REGISTRIES_DIR,
        queries_dir=QUERIES_DIR,
        bundle_dir=BUNDLES_DIR,
    )
    if rolled:
        console.print(f"[green]Rolled back[/green] {profile_name}.")
    else:
        console.print(
            f"[dim]Nothing to roll back for {profile_name}.[/dim]"
        )


# ─── status ───────────────────────────────────────────────────────────


def bundle_status_command(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p",
        help="Profile to inspect (default: active profile)",
    ),
) -> None:
    """Show the currently-applied bundle's manifest, if any."""
    profile_name = profile or state.active_profile_name
    manifest = load_local_manifest(BUNDLES_DIR, profile_name)
    if not manifest:
        console.print(
            f"[dim]No bundle installed for {profile_name}.[/dim]\n"
            "[dim]Run `bcli config refresh` once your team has published one.[/dim]"
        )
        return
    out.print(f"[bold]bundle:[/bold] {profile_name}")
    out.print(f"  version:        {manifest.version}")
    out.print(f"  published_at:   {manifest.published_at:%Y-%m-%d %H:%M %Z}")
    if manifest.publisher:
        out.print(f"  publisher:      {manifest.publisher}")
    if manifest.previous_version:
        out.print(f"  previous:       {manifest.previous_version}")
    if manifest.release_notes:
        out.print(f"  notes:          {manifest.release_notes}")
    out.print(f"  checksum_sha256: {manifest.checksum_sha256[:16]}…")


# ─── make-bundle (admin) ──────────────────────────────────────────────


def make_bundle_command(
    source_dir: Path = typer.Argument(
        ...,
        help="Directory containing registry.json, queries.yaml, field_lists.json",
    ),
    profile: str = typer.Option(..., "--profile", "-p", help="Profile name"),
    version: str = typer.Option(..., "--version", help="Bundle version (e.g. 2026.05.07-1)"),
    publisher: str = typer.Option("", "--publisher", help="Who is publishing this bundle"),
    notes: str = typer.Option("", "--notes", help="Short release-notes line"),
    previous: str = typer.Option("", "--previous", help="Previous bundle version, for changelog continuity"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Output tarball path (default: <profile>-<version>.tar.gz)",
    ),
) -> None:
    """Build a tarball bundle from a directory tree.

    \b
    Example:
      bcli config make-bundle ./bundle-finance \\
        --profile finance --version 2026.05.07-1 \\
        --publisher ops-bcli-bot \\
        --notes "Added overdue-ic and posted-by-id queries" \\
        --output finance-2026.05.07-1.tar.gz
    """
    try:
        path, manifest = make_bundle(
            source_dir,
            profile=profile,
            version=version,
            publisher=publisher,
            release_notes=notes,
            previous_version=previous,
            output_path=output,
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]make-bundle failed:[/red] {e}")
        raise typer.Exit(1) from e

    console.print(f"[green]Built[/green] {path}")
    console.print(f"  profile:  {manifest.profile}")
    console.print(f"  version:  {manifest.version}")
    console.print(f"  checksum: {manifest.checksum_sha256[:16]}…")
    console.print(f"  files:    {len(manifest.contents)}")
