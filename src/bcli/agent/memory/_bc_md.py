"""Per-profile BC.md memory loader (manual-first, read-only in v1).

Resolution order (first hit wins):

1. Project-local ``./BC.md`` — walks up from the current directory, the
   same way ``.bcli.toml`` is discovered. Lets a repo pin agent context
   ("our vendors are keyed by displayName, never by number").
2. ``~/.config/bcli/profiles/<profile>/BC.md`` — the per-profile memory.

The loaded text is injected into the system prompt after the base
instructions and before the context bundle. Size-capped so a runaway
file can't blow the prompt budget.
"""

from __future__ import annotations

from pathlib import Path

from bcli.config._defaults import CONFIG_DIR

MAX_BC_MD_BYTES = 16 * 1024


def profile_bc_md_path(profile_name: str, config_dir: Path | None = None) -> Path:
    base = config_dir or CONFIG_DIR
    return base / "profiles" / profile_name / "BC.md"


def _find_project_bc_md(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        path = candidate / "BC.md"
        if path.is_file():
            return path
    return None


def load_bc_md(
    profile_name: str,
    *,
    cwd: Path | None = None,
    config_dir: Path | None = None,
) -> str:
    """Load BC.md memory text (empty string when none exists)."""
    candidates: list[Path] = []
    project = _find_project_bc_md(cwd or Path.cwd())
    if project is not None:
        candidates.append(project)
    if profile_name:
        candidates.append(profile_bc_md_path(profile_name, config_dir))

    for path in candidates:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                if len(text.encode("utf-8")) > MAX_BC_MD_BYTES:
                    text = text.encode("utf-8")[:MAX_BC_MD_BYTES].decode(
                        "utf-8", errors="ignore",
                    ) + "\n\n[BC.md truncated at 16 KiB]"
                return text
        except OSError:
            continue
    return ""


__all__ = ["MAX_BC_MD_BYTES", "load_bc_md", "profile_bc_md_path"]
