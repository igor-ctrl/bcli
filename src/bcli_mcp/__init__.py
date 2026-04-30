"""bcli-mcp — MCP (Model Context Protocol) server that wraps the bcli CLI.

The server exposes a small, deliberately tight surface (4 read-only tools) that
let an MCP-aware client (Claude Desktop, MCP Inspector, etc.) drive bcli.
Every tool subprocesses ``bcli ... --format json`` so profile resolution,
auth, retry, telemetry, and the read-only ``disable_writes`` gate are
inherited from the CLI for free.

This package is preview/experimental in 0.2.0. See ``docs/mcp-server.md``.
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:
    """Entry point used by the ``bcli-mcp`` console script."""
    from bcli_mcp.__main__ import main as _main

    _main()
