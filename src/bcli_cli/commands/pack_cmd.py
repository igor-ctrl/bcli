"""``bcli pack`` — list / info / install / uninstall packs (Part 1).

The CLI surface for the SDK in :mod:`bcli.packs`. Mirrors the
``skill_init`` UX where reasonable: confirmation prompts, atomic
writes, ledger-driven cleanup. Reads the active profile name from
``state`` so the user doesn't have to repeat ``--profile`` on each
sub-command.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bcli.packs import (
    InstallError,
    Pack,
    PackLoadError,
    discover_all,
    install_pack,
    list_ledgers,
    load_pack,
    read_ledger,
    uninstall_pack,
)
from bcli_cli._state import state

app = typer.Typer(no_args_is_help=True, help="Manage bcli packs (queries, batches, fragments)")
console = Console()
_stderr = Console(stderr=True)
logger = logging.getLogger("bcli.packs")


def _resolve_profile(profile_flag: str | None) -> str:
    """Profile name → from flag, then state, then config default."""
    if profile_flag:
        return profile_flag
    if state.profile_name:
        return state.profile_name
    try:
        cfg = state.config
        return cfg.defaults.profile
    except Exception:  # noqa: BLE001
        return "default"


def _load_or_resolve(
    name_or_path: str, available: dict[str, Pack] | None = None
) -> Pack:
    """Resolve ``name_or_path`` to a :class:`Pack`.

    A path is loaded directly; a bare name is looked up in the
    discovery registry.
    """
    p = Path(name_or_path)
    if p.exists():
        return load_pack(p)
    packs = available if available is not None else discover_all()
    if name_or_path not in packs:
        suggestions = ", ".join(sorted(packs.keys())) or "(none)"
        raise typer.BadParameter(
            f"pack {name_or_path!r} not found. Available: {suggestions}"
        )
    return packs[name_or_path]


# ─── list ───────────────────────────────────────────────────────────


@app.command("list")
def list_cmd(
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
) -> None:
    """Show every pack the CLI can see (built-in + entry-point)."""
    packs = discover_all()
    if not packs:
        _stderr.print(
            "[yellow]No packs available. Built-ins ship under packs/ "
            "in the repo; third-party packages register via the "
            "'bcli.packs' entry-point group.[/yellow]"
        )
        return

    resolved_profile = _resolve_profile(profile)
    installed = {
        led.pack_name: led for led in list_ledgers(resolved_profile)
    }

    table = Table(title=f"bcli packs (profile: {resolved_profile})")
    table.add_column("name", style="bold")
    table.add_column("version")
    table.add_column("installed")
    table.add_column("description")
    for name in sorted(packs.keys()):
        p = packs[name]
        installed_marker = (
            installed[name].pack_version
            if name in installed else "[dim]—[/dim]"
        )
        table.add_row(
            name,
            p.version,
            installed_marker,
            p.manifest.description or "",
        )
    console.print(table)


# ─── info ───────────────────────────────────────────────────────────


@app.command("info")
def info_cmd(
    name: str = typer.Argument(..., help="Pack name (or path)"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
) -> None:
    """Show pack manifest + a diff against the live ledger."""
    pack = _load_or_resolve(name)
    resolved_profile = _resolve_profile(profile)
    led = read_ledger(pack.name, resolved_profile)

    console.print(f"[bold]{pack.name}[/bold]  [dim]v{pack.version}[/dim]")
    if pack.manifest.description:
        console.print(pack.manifest.description)
    if pack.manifest.target_profile:
        console.print(
            f"[dim]suggested target profile: "
            f"{pack.manifest.target_profile}[/dim]"
        )
    console.print()
    console.print(
        f"  fragments:        {len(pack.contents.agent_fragments)}"
    )
    console.print(f"  queries:          {len(pack.contents.queries)}")
    console.print(f"  batches:          {len(pack.contents.batches)}")
    console.print(
        f"  registry_presets: {len(pack.contents.registry_presets)}"
    )
    if pack.manifest.recommended_context_providers:
        console.print(
            "  recommended context providers: "
            + ", ".join(pack.manifest.recommended_context_providers)
        )

    console.print()
    if led is None:
        console.print(
            f"[yellow]Not installed on profile {resolved_profile!r}.[/yellow]"
        )
    else:
        console.print(
            f"[green]Installed v{led.pack_version} at {led.installed_at}[/green]"
        )
        console.print(f"  target: {led.target}")
        if led.pack_version != pack.version:
            console.print(
                f"[yellow]  ▲ installed version differs from source "
                f"(installed: {led.pack_version}, source: {pack.version})[/yellow]"
            )


# ─── install ────────────────────────────────────────────────────────


@app.command("install")
def install_cmd(
    name: str = typer.Argument(..., help="Pack name or local directory"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
    target: Optional[Path] = typer.Option(
        None, "--target", help="Install root (defaults to project or $HOME)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    replace_owned: bool = typer.Option(
        False, "--replace-owned",
        help="Allow overwriting endpoints owned by another pack",
    ),
    accept_conflicts: bool = typer.Option(
        False, "--accept-conflicts",
        help="Required alongside --replace-owned to acknowledge the diff",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip interactive confirmation",
    ),
) -> None:
    """Install a pack's contents under the active profile."""
    pack = _load_or_resolve(name)
    resolved_profile = _resolve_profile(profile)

    try:
        plan = install_pack(
            pack,
            profile=resolved_profile,
            target=target,
            dry_run=True,  # always plan first
        )
    except (InstallError, PackLoadError) as exc:
        _stderr.print(f"[red]Install failed: {exc}[/red]")
        raise typer.Exit(code=1)

    _render_plan(plan)

    if plan.conflicts and not (replace_owned and accept_conflicts):
        _stderr.print(
            "\n[red]Refusing to overwrite endpoints owned by another pack."
            " Pass --replace-owned --accept-conflicts to override.[/red]"
        )
        raise typer.Exit(code=1)

    if dry_run:
        console.print("\n[dim]Dry run — nothing written.[/dim]")
        return

    if not yes:
        confirmed = typer.confirm(
            f"Install {pack.name} v{pack.version} into "
            f"profile {resolved_profile!r}?",
            default=True,
        )
        if not confirmed:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(code=1)

    try:
        install_pack(
            pack,
            profile=resolved_profile,
            target=target,
            dry_run=False,
            replace_owned=replace_owned,
            accept_conflicts=accept_conflicts,
        )
    except InstallError as exc:
        _stderr.print(f"[red]Install failed: {exc}[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"[green]Installed {pack.name} v{pack.version} on "
        f"profile {resolved_profile!r}.[/green]"
    )
    if pack.manifest.recommended_context_providers:
        console.print(
            "[dim]This pack recommends enabling these `bcli ask` "
            "context providers (opt-in via [ask] context_providers in "
            "config): "
            + ", ".join(pack.manifest.recommended_context_providers)
            + "[/dim]"
        )


# ─── uninstall ──────────────────────────────────────────────────────


@app.command("uninstall")
def uninstall_cmd(
    name: str = typer.Argument(...),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Remove a previously installed pack via its ledger."""
    resolved_profile = _resolve_profile(profile)
    led = read_ledger(name, resolved_profile)
    if led is None:
        _stderr.print(
            f"[yellow]Pack {name!r} is not installed on profile "
            f"{resolved_profile!r}.[/yellow]"
        )
        raise typer.Exit(code=1)
    if not yes:
        if not typer.confirm(
            f"Uninstall {name} v{led.pack_version} from profile "
            f"{resolved_profile!r}? "
            f"({len(led.paths)} artefacts, "
            f"{len(led.registry_endpoints)} endpoints)",
            default=False,
        ):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(code=1)
    result = uninstall_pack(name, profile=resolved_profile)
    console.print(
        f"[green]Removed {len(result.files_removed)} files, "
        f"{len(result.blocks_removed)} marker blocks.[/green]"
    )
    if result.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  - {w}")


# ─── Rendering helpers ──────────────────────────────────────────────


def _render_plan(plan) -> None:
    """Pretty-print an :class:`InstallPlan` for human review."""
    p = plan.pack
    console.print(
        f"\n[bold]Plan for {p.name} v{p.version}[/bold]"
        f" → profile [italic]{plan.profile}[/italic]"
    )
    console.print(f"target dir: {plan.target}")
    if plan.fragment_writes:
        console.print(f"\nAgent fragments ({len(plan.fragment_writes)}):")
        for pf in plan.fragment_writes:
            console.print(
                f"  [dim]write[/dim] {pf.path}"
                f" [dim]({pf.fragment.targets})[/dim]"
            )
    if plan.block_writes:
        console.print(f"\nMarker blocks ({len(plan.block_writes)}):")
        for blk in plan.block_writes:
            console.print(f"  [dim]splice[/dim] {blk.target_file} ← {blk.block_id}")
    if plan.query_writes:
        console.print(f"\nSaved queries ({len(plan.query_writes)}):")
        for pq in plan.query_writes:
            console.print(f"  [dim]merge[/dim] {pq.query.name}")
    if plan.batch_writes:
        console.print(f"\nBatches ({len(plan.batch_writes)}):")
        for pb in plan.batch_writes:
            console.print(f"  [dim]write[/dim] {pb.path}")
    if plan.preset_writes:
        console.print(f"\nRegistry presets ({len(plan.preset_writes)}):")
        for ps in plan.preset_writes:
            console.print(f"  [dim]merge[/dim] {ps.name} → {ps.target_path}")
    if plan.conflicts:
        console.print(
            f"\n[red]Conflicts ({len(plan.conflicts)}):[/red]"
        )
        for c in plan.conflicts:
            console.print(
                f"  - {c.endpoint} owned by "
                f"{c.incumbent_pack} v{c.incumbent_version}"
            )


__all__ = ["app"]
