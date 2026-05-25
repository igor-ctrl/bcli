"""Pack data shapes (R3, R7, R8).

A *pack* is a versioned bundle of:

* saved-query YAML entries (merged into ``~/.config/bcli/queries/<profile>.yaml``)
* batch templates (copied to ``~/.config/bcli/batches/<profile>/``)
* AGENTS.md / CLAUDE.md fragments (per-fragment ``targets:`` declaration,
  default ``[agents]`` — R3)
* registry presets (merged into ``~/.config/bcli/registries/<profile>.json``
  with provenance tags — R7)

Packs ship from three sources, in lookup order:

1. **Built-in** — ``packs/`` at the repo root, shipped in the wheel.
2. **Entry-point** — third-party packages register via the
   ``bcli.packs`` group (R8).
3. **Local path** — ``bcli pack install --path <dir>`` for development.

All dataclasses are frozen so a loaded pack can be passed across
boundaries without accidental mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ─── Constants ──────────────────────────────────────────────────────


# Fragment targets (R3). Default `[agents]` because most fragments
# describe operational verbs which live in AGENTS.md.
TARGET_AGENTS = "agents"
TARGET_CLAUDE = "claude"
VALID_TARGETS = frozenset({TARGET_AGENTS, TARGET_CLAUDE})


# ─── Fragments ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentFragment:
    """One Markdown fragment shipped by a pack.

    ``content`` is the rendered body the installer will inline into the
    chosen target files (per :attr:`targets`). ``name`` becomes the
    filename under ``<target>/.claude/agent.d/bcli-<pack>/``.

    ``targets`` defaults to ``("agents",)`` — operational guidance
    lives in AGENTS.md by default. A pack author opts in to
    ``CLAUDE.md`` (or both) per fragment when the content is
    Claude-Code-specific (routing rules, slash-command behaviour).
    """

    name: str
    content: str
    targets: tuple[str, ...] = (TARGET_AGENTS,)
    description: str = ""

    def __post_init__(self) -> None:  # noqa: D401
        # Frozen dataclass workaround for validation — object.__setattr__
        # only allowed inside __post_init__.
        bad = [t for t in self.targets if t not in VALID_TARGETS]
        if bad:
            raise ValueError(
                f"fragment {self.name!r}: invalid targets {bad}; "
                f"expected subset of {sorted(VALID_TARGETS)}"
            )
        if not self.targets:
            object.__setattr__(self, "targets", (TARGET_AGENTS,))


# ─── Saved queries / batches / registry presets ─────────────────────


@dataclass(frozen=True)
class PackQuery:
    """One saved-query entry. Body matches ``bcli q`` YAML shape."""

    name: str
    body: dict[str, Any]


@dataclass(frozen=True)
class PackBatch:
    """One batch YAML template (filename + raw text body)."""

    filename: str
    body: str


@dataclass(frozen=True)
class PackRegistryPreset:
    """A custom registry endpoint declaration carried by a pack (R7).

    The installer injects ``source_pack`` and ``pack_version``
    provenance into each entry before writing into
    ``~/.config/bcli/registries/<profile>.json``. Conflict detection
    refuses to overwrite an endpoint owned by a different pack
    unless ``--replace-owned --accept-conflicts`` is passed.
    """

    name: str
    body: dict[str, Any]


# ─── Manifest + Pack ────────────────────────────────────────────────


@dataclass(frozen=True)
class PackManifest:
    """The parsed ``pack.yaml`` head fields — everything except contents.

    ``recommended_context_providers`` (R8) is informational only —
    pack install never auto-enables a provider in ``[ask]
    context_providers``; the user opts in deliberately.
    """

    name: str
    version: str
    description: str = ""
    target_profile: str = ""
    recommended_context_providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackContents:
    """Loaded body content of a pack — fragments, queries, batches, presets."""

    agent_fragments: tuple[AgentFragment, ...] = ()
    queries: tuple[PackQuery, ...] = ()
    batches: tuple[PackBatch, ...] = ()
    registry_presets: tuple[PackRegistryPreset, ...] = ()


@dataclass(frozen=True)
class Pack:
    """A fully loaded pack — manifest + contents.

    ``source_path`` is the directory the pack was loaded from when
    relevant (built-in or local). Entry-point packs may set it
    empty.
    """

    manifest: PackManifest
    contents: PackContents = field(default_factory=PackContents)
    source_path: Path | None = None

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def version(self) -> str:
        return self.manifest.version


__all__ = [
    "AgentFragment",
    "Pack",
    "PackBatch",
    "PackContents",
    "PackManifest",
    "PackQuery",
    "PackRegistryPreset",
    "TARGET_AGENTS",
    "TARGET_CLAUDE",
    "VALID_TARGETS",
]
