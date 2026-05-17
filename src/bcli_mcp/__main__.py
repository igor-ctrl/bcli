"""``python -m bcli_mcp`` / ``bcli-mcp`` entry point.

Resets cwd to the user's home directory before constructing the FastMCP
server. Why: ``bcli`` config loading auto-discovers ``.bcli.toml`` from cwd
upward (see ``src/bcli/config/_loader.py``). Claude Desktop launches MCP
servers with whatever cwd it was started from, which could be a directory
containing a hostile or stale project-level ``.bcli.toml``. Pinning cwd to
``$HOME`` makes ``~/.config/bcli/config.toml`` the only trusted config
source for every subprocess this MCP launches.
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    # Trust-model: chdir BEFORE any bcli import or FastMCP instantiation
    # so any side effects of import time (config probes, registry loads)
    # also see the safe cwd.
    os.chdir(os.path.expanduser("~"))

    try:
        from bcli_mcp._server import get_server
    except ImportError as exc:
        sys.stderr.write(
            f"bcli-mcp: failed to import server: {exc}\n"
            "Hint: install the MCP extra: pip install 'bc-cli[mcp]'\n"
        )
        raise SystemExit(1) from exc

    get_server().run()


if __name__ == "__main__":
    main()
