"""``bcli.packs`` — pack mechanism (Part 1 / R2, R3, R7, R8).

A *pack* is a versioned bundle of saved queries, batch templates,
registry presets and agent fragments. Packs ship from three sources:

* Built-in (``packs/`` directory in the repo)
* Entry-point group ``bcli.packs`` (third-party packages)
* Local path (``bcli pack install --path <dir>``)

This package is the SDK surface; the CLI command lives at
``bcli_cli.commands.pack_cmd``.
"""

from __future__ import annotations

from bcli.packs._installer import (
    EndpointConflict,
    InstallError,
    InstallPlan,
    UninstallResult,
    install_pack,
    plan_install,
    uninstall_pack,
)
from bcli.packs._ledger import (
    Ledger,
    LedgerEntry,
    LedgerRegistryEntry,
    list_ledgers,
    read_ledger,
)
from bcli.packs._loader import PackLoadError, load_pack
from bcli.packs._protocol import (
    AgentFragment,
    Pack,
    PackBatch,
    PackContents,
    PackManifest,
    PackQuery,
    PackRegistryPreset,
    TARGET_AGENTS,
    TARGET_CLAUDE,
)
from bcli.packs._registry import (
    ENTRYPOINT_GROUP,
    builtin_packs_dir,
    discover_all,
    discover_builtin_packs,
    discover_entrypoint_packs,
)

__all__ = [
    "AgentFragment",
    "ENTRYPOINT_GROUP",
    "EndpointConflict",
    "InstallError",
    "InstallPlan",
    "Ledger",
    "LedgerEntry",
    "LedgerRegistryEntry",
    "Pack",
    "PackBatch",
    "PackContents",
    "PackLoadError",
    "PackManifest",
    "PackQuery",
    "PackRegistryPreset",
    "TARGET_AGENTS",
    "TARGET_CLAUDE",
    "UninstallResult",
    "builtin_packs_dir",
    "discover_all",
    "discover_builtin_packs",
    "discover_entrypoint_packs",
    "install_pack",
    "list_ledgers",
    "load_pack",
    "plan_install",
    "read_ledger",
    "uninstall_pack",
]
