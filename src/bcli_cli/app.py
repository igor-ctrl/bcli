"""bcli CLI — root Typer application."""

from __future__ import annotations

import atexit
import logging
import sys
import time
from typing import Optional

import typer

from bcli._version import __version__
from bcli_cli._state import state
from bcli_cli.output import detect_default_format

_invocation_started_at: float | None = None
_invocation_command: str = ""


def _enable_debug_logging() -> None:
    """Stream the structured `bcli.*` logs to stderr at DEBUG level.

    The transport already emits per-request JSON to `bcli.http`; without a
    handler attached those records go nowhere. Wire one up so `--debug`
    actually produces visible output.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    for name in ("bcli", "bcli.http", "bcli.auth", "bcli.client"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        # Avoid duplicate handlers when the callback runs more than once
        # (e.g. inside the Typer test runner across multiple invocations).
        if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
            logger.addHandler(handler)
        logger.propagate = False

app = typer.Typer(
    name="bcli",
    help=(
        "CLI for Microsoft Dynamics 365 Business Central APIs.\n\n"
        "[bold]Discovery (handy for AI agents driving bcli):[/bold]\n"
        "  bcli endpoint search <pattern>   fuzzy-find an endpoint\n"
        "  bcli endpoint info <name> -f json   structured metadata\n"
        "  bcli endpoint fields <name>      discover real field names "
        "(don't guess)\n"
        "  --profile <name> alone is enough — environment, company, and\n"
        "  client_id resolve from the profile. Pass -e only to [italic]override[/italic]."
    ),
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
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, markdown, json, csv, ndjson, raw (auto-detects markdown for non-TTY/AI agents)"),
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
    resolved_format = format or detect_default_format()
    state.format = resolved_format
    state.verbose = verbose
    state.debug = debug
    state.dry_run = dry_run
    if debug:
        _enable_debug_logging()
    # Auto-quiet for machine-readable formats (banner goes to stderr but still
    # confuses piped output when both streams are captured together). Markdown
    # stays loud — humans and agents both benefit from the context banner.
    state.quiet = quiet or resolved_format in ("json", "csv", "ndjson", "raw")

    _bootstrap_telemetry()


def _bootstrap_telemetry() -> None:
    """Emit `bcli.startup` and register a `bcli.command` summary on exit.

    Wrapped in a try/except so a misconfigured telemetry section never
    prevents the CLI from running. NullSink (the default) makes both
    emit() and flush() no-ops, so the `try` body is essentially free.
    """
    global _invocation_started_at, _invocation_command
    try:
        from bcli.telemetry import events

        _invocation_started_at = time.monotonic()
        # `sys.argv[1:]` is the user-visible command (subcommand + flags).
        # Trim flags for the per-command rollup so "get vendors --top 5"
        # gets bucketed under "get vendors" rather than every flag combo.
        argv = [a for a in sys.argv[1:] if not a.startswith("-")]
        _invocation_command = " ".join(argv[:2])

        sink = state.telemetry
        if not sink.is_active:
            return  # NullSink — skip both startup + atexit registration.

        sink.emit(*events.startup(
            profile=state.profile_name or "",
            environment=state.env_override or "",
            command=_invocation_command,
        ))
        atexit.register(_emit_command_summary)
    except Exception:  # noqa: BLE001
        # Telemetry must never crash the CLI.
        logging.getLogger("bcli.telemetry").debug("telemetry bootstrap failed", exc_info=True)


def _emit_command_summary() -> None:
    """Atexit hook: ship a `bcli.command` event with the total duration."""
    try:
        from bcli.telemetry import events

        if _invocation_started_at is None:
            return
        duration_ms = (time.monotonic() - _invocation_started_at) * 1000.0

        sink = state.telemetry
        if not sink.is_active:
            return
        # Determine status from sys.exc_info() — atexit runs after exception
        # propagation, so a non-None exception means the command errored.
        exc_type = sys.exc_info()[0]
        status = "error" if exc_type is not None else "ok"

        sink.emit(*events.command(
            command=_invocation_command,
            profile=state.profile_name or "",
            environment=state.env_override or "",
            company_alias=state.company_override or "",
            duration_ms=duration_ms,
            status=status,
        ))
        sink.flush()
    except Exception:  # noqa: BLE001
        logging.getLogger("bcli.telemetry").debug("telemetry summary failed", exc_info=True)


# Import and register command groups
from bcli_cli.commands import (  # noqa: E402
    attach_cmd,
    auth_cmd,
    batch_cmd,
    company_cmd,
    config_cmd,
    context_cmd,
    delete_cmd,
    endpoint_cmd,
    env_cmd,
    get_cmd,
    patch_cmd,
    post_cmd,
    query_cmd,
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
app.add_typer(attach_cmd.app, name="attach", help="Document-attachment workflows (two-phase /attachments upload)")
app.command(name="get")(get_cmd.get_command)
app.command(name="post")(post_cmd.post_command)
app.command(name="patch")(patch_cmd.patch_command)
app.command(name="delete")(delete_cmd.delete_command)
app.command(name="q", help="Run a saved query (no OData required)")(query_cmd.query_command)
app.command(name="ai-context")(context_cmd.ai_context_command)

# ETL command — optional, only available when dlt is installed
try:
    from bcli_cli.commands import etl_cmd
    app.add_typer(etl_cmd.app, name="etl", help="ETL pipeline (requires dlt)")
except ImportError:
    pass


def main() -> None:
    """Console-script entry point.

    Wraps the Typer ``app`` with a SIGPIPE handler so that ``bcli ... | head``
    and similar pipe-truncating consumers terminate the CLI silently —
    matching the Unix idiom of ``cat``, ``grep`` and friends — instead of
    surfacing ``BrokenPipeError`` at interpreter shutdown.

    On Windows the ``signal.SIGPIPE`` constant is absent; the safety-net
    ``try`` below catches the error in that path.
    """
    import signal

    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    try:
        app()
    except BrokenPipeError:
        # Closing stdout/stderr before exit prevents Python's atexit flush
        # from re-triggering the same error on a now-dead pipe.
        try:
            sys.stdout.close()
        except Exception:
            pass
        try:
            sys.stderr.close()
        except Exception:
            pass
        sys.exit(0)


if __name__ == "__main__":
    main()
