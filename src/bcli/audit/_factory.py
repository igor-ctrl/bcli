"""Build an :class:`AuditSink` from an :class:`AuditConfig`.

Returns :class:`NullAuditSink` when audit is disabled or the chosen
backend cannot be loaded — callers can ``emit()`` unconditionally.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from bcli.audit._protocol import AuditSink, JSONLAuditSink, NullAuditSink

if TYPE_CHECKING:
    from bcli.config._model import AuditConfig

logger = logging.getLogger("bcli.audit")


def get_audit_sink(
    config: "AuditConfig | None",
    *,
    profile_name: str,
) -> AuditSink:
    """Build an audit sink for the given profile.

    ``profile_name`` is interpolated into ``config.path`` (template token
    ``{profile}``) so a single global config produces one file per
    profile automatically.
    """
    if config is None or not config.enabled:
        return NullAuditSink()

    backend = (config.backend or "jsonl").strip().lower()
    if backend == "null":
        return NullAuditSink()

    if backend == "jsonl":
        try:
            path = _resolve_path(config.path, profile_name=profile_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "audit path resolution failed (%s); falling back to NullAuditSink", exc
            )
            return NullAuditSink()
        return JSONLAuditSink(
            path=path,
            max_size_bytes=int(config.max_size_mb) * 1024 * 1024,
        )

    logger.warning(
        "unknown audit backend '%s'; falling back to NullAuditSink. "
        "Built-in choices: 'jsonl', 'null'.",
        config.backend,
    )
    return NullAuditSink()


def _resolve_path(
    template: str | None,
    *,
    profile_name: str,
) -> Path:
    """Expand the configured path, falling back to the documented default
    when the user didn't set one."""
    if template:
        expanded = template.format(profile=profile_name)
    else:
        # Default: ~/.config/bcli/audit/{profile}.jsonl
        expanded = str(
            Path.home() / ".config" / "bcli" / "audit" / f"{profile_name}.jsonl"
        )
    return Path(expanded).expanduser()
