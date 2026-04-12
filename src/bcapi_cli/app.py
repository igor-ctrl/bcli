"""bcapi CLI — root Typer application."""

from __future__ import annotations

from typing import Optional

import typer

from bcapi._version import __version__
from bcapi_cli._state import state

app = typer.Typer(
    name="bcli",
    help="CLI for Microsoft Dynamics 365 Business Central APIs",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"bcli {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Connection profile name"),
    env: Optional[str] = typer.Option(None, "--env", "-e", help="Override environment name"),
    company: Optional[str] = typer.Option(None, "--company", "-c", help="Override company ID"),
    format: str = typer.Option("table", "--format", "-f", help="Output format: table, json, csv, ndjson, raw"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show resolved URLs and timing"),
    debug: bool = typer.Option(False, "--debug", help="Show full HTTP request/response"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would execute"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress context banner"),
    version: bool = typer.Option(False, "--version", "-V", callback=version_callback, is_eager=True),
) -> None:
    """Global options applied to all commands."""
    state.profile_name = profile
    state.env_override = env
    state.company_override = company
    state.format = format
    state.verbose = verbose
    state.debug = debug
    state.dry_run = dry_run
    state.quiet = quiet


# Import and register command groups
from bcapi_cli.commands import (  # noqa: E402
    auth_cmd,
    batch_cmd,
    company_cmd,
    config_cmd,
    delete_cmd,
    endpoint_cmd,
    env_cmd,
    get_cmd,
    patch_cmd,
    post_cmd,
    registry_cmd,
    test_cmd,
)

app.add_typer(config_cmd.app, name="config", help="Configuration management")
app.add_typer(auth_cmd.app, name="auth", help="Authentication")
app.add_typer(env_cmd.app, name="env", help="Environment discovery and selection")
app.add_typer(company_cmd.app, name="company", help="Company discovery and selection")
app.add_typer(endpoint_cmd.app, name="endpoint", help="Endpoint discovery")
app.add_typer(registry_cmd.app, name="registry", help="Custom API registry management")
app.add_typer(test_cmd.app, name="test", help="Connection and endpoint testing")
app.add_typer(batch_cmd.app, name="batch", help="Batch operations from YAML files")
app.command(name="get")(get_cmd.get_command)
app.command(name="post")(post_cmd.post_command)
app.command(name="patch")(patch_cmd.patch_command)
app.command(name="delete")(delete_cmd.delete_command)


if __name__ == "__main__":
    app()
