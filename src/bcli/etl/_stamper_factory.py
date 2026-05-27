"""Pluggable stamper discovery for the BC ETL source.

OSS ships the *mechanism*; downstream packages register vendor-specific
audit columns. A third-party package exposes a zero-arg callable that
returns a :data:`~bcli.etl._stampers.Stamper` and advertises it under
the ``bcli.etl.stampers`` entry-point group::

    [project.entry-points."bcli.etl.stampers"]
    audit = "my_pkg.etl:audit_stamper"

The user opts in by name via ``[etl] stampers = ["audit"]`` in
``~/.config/bcli/config.toml``. :func:`build_stampers` resolves that
name list to concrete stampers, applied in the order given.

Mirrors the dispatch shape of :mod:`bcli.telemetry._factory` and
:mod:`bcli.ask._providers`: an unknown name or a failing factory logs a
warning and is skipped — one broken plugin never aborts a sync.

This module is part of the generic layer and must not import from bcli.*.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Callable

from bcli.etl._stampers import Stamper

logger = logging.getLogger("bcli.etl")

ENTRYPOINT_GROUP = "bcli.etl.stampers"

# A factory is a zero-arg callable returning a Stamper.
StamperFactory = Callable[[], Stamper]


def discover_stamper_factories() -> dict[str, StamperFactory]:
    """Return ``{name: factory}`` for every registered ``bcli.etl.stampers``.

    A factory that fails to load logs a warning and is skipped.
    """
    out: dict[str, StamperFactory] = {}
    for ep in _iter_entrypoints():
        try:
            factory = ep.load()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bcli.etl.stampers entry-point %r failed to load: %s",
                ep.name, exc,
            )
            continue
        if not callable(factory):
            logger.warning(
                "bcli.etl.stampers entry-point %r is not callable; skipping.",
                ep.name,
            )
            continue
        out[ep.name] = factory
    return out


def build_stampers(names: list[str]) -> list[Stamper]:
    """Resolve a list of entry-point names to concrete stampers, in order.

    Unknown names and factories that raise are logged and skipped so a
    single misconfigured plugin can't abort the whole sync.
    """
    if not names:
        return []
    available = discover_stamper_factories()
    out: list[Stamper] = []
    for name in names:
        factory = available.get(name)
        if factory is None:
            logger.warning(
                "ETL stamper %r is not registered (available: %s); skipping.",
                name, sorted(available) or "none",
            )
            continue
        try:
            out.append(factory())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ETL stamper %r factory raised %s; skipping.", name, exc,
            )
    return out


def _iter_entrypoints():
    try:
        yield from entry_points(group=ENTRYPOINT_GROUP)
    except Exception:  # pragma: no cover — defensive
        return


__all__ = [
    "ENTRYPOINT_GROUP",
    "StamperFactory",
    "build_stampers",
    "discover_stamper_factories",
]
