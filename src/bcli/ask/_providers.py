"""Optional ``bcli.ask.context_providers`` entry-point group (R8).

A *context provider* is a callable a downstream package registers
under the ``bcli.ask.context_providers`` group. The provider
receives the current profile + last-error snapshot and returns a
flat ``dict[str, str]`` of additional context the operator wants
fed into the bundle (glossary terms, company aliases, schema
hints, etc.).

Providers are opt-in per-user via ``[ask] context_providers = [...]``
in the config — the installer NEVER auto-enables them (this is
the R8 boundary).
"""

from __future__ import annotations

import logging
from importlib.metadata import EntryPoint, entry_points
from typing import Callable, Iterable, Iterator

from bcli.context import LastErrorRecord, ProfileSnapshot

logger = logging.getLogger("bcli.ask.providers")

ENTRYPOINT_GROUP = "bcli.ask.context_providers"

# Type alias for the provider signature.
ProviderFn = Callable[[ProfileSnapshot, LastErrorRecord | None], dict[str, str]]


def _iter_entrypoints() -> Iterator[EntryPoint]:
    try:
        yield from entry_points(group=ENTRYPOINT_GROUP)
    except Exception:  # pragma: no cover — defensive
        return


def discover_providers() -> dict[str, ProviderFn]:
    """Return ``{name: callable}`` for every registered provider.

    A failing entry-point logs a warning and is skipped.
    """
    out: dict[str, ProviderFn] = {}
    for ep in _iter_entrypoints():
        try:
            fn = ep.load()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "bcli.ask.context_providers entry-point %r failed to load: %s",
                ep.name, exc,
            )
            continue
        if not callable(fn):
            logger.warning(
                "bcli.ask.context_providers entry-point %r is not callable",
                ep.name,
            )
            continue
        out[ep.name] = fn
    return out


def collect_extra_context(
    *,
    profile: ProfileSnapshot,
    last_error: LastErrorRecord | None,
    enabled: Iterable[str],
) -> dict[str, str]:
    """Run only the providers the user opted into.

    Each provider's output dict is shallow-merged into the result.
    Later providers override earlier keys (rare; documented for
    reproducibility).
    """
    available = discover_providers()
    out: dict[str, str] = {}
    for name in enabled:
        fn = available.get(name)
        if fn is None:
            logger.debug(
                "context provider %r not installed; skipping", name
            )
            continue
        try:
            payload = fn(profile, last_error) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "context provider %r raised %s; skipping", name, exc
            )
            continue
        if not isinstance(payload, dict):
            logger.warning(
                "context provider %r returned non-dict; skipping", name
            )
            continue
        for k, v in payload.items():
            out[str(k)] = str(v)
    return out


__all__ = [
    "ENTRYPOINT_GROUP",
    "ProviderFn",
    "collect_extra_context",
    "discover_providers",
]
