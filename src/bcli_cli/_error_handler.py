"""Centralized CLI error handling — exit code + remediation hint.

AIP §Phase 4c: every error names the fix. Rather than touch every raise
site, we catch :class:`BCLIError` subclasses at the outer ``main()``
boundary and:

1. Map the exception class to a documented exit code (taxonomy from
   §Phase 4a).
2. Append a short, executable remediation hint after the message — the
   "Did you mean ..." extension to auth, registry, and profile errors.

The handler is idempotent: if the upstream raise site already named the
fix (e.g. ``ConfigError("Run 'bcli config init' ...")``), we don't append
a duplicate. The fuzzy-match path on :class:`RegistryError` stays in the
raise site (``EndpointRegistry.resolve``); this module only adds the
"no fuzzy match → try import" fallback.
"""

from __future__ import annotations

import difflib

from bcli.errors import (
    AuthError,
    BCLIError,
    ConfigError,
    ForbiddenError,
    NotFoundError,
    RegistryError,
    SafetyError,
    ValidationError,
)
from bcli.exit_codes import (
    EXIT_AUTH,
    EXIT_GENERIC_ERROR,
    EXIT_NOT_FOUND,
    EXIT_POLICY,
    EXIT_USAGE,
    EXIT_VALIDATION,
    exit_code_for_status,
)


# ─── Exit-code mapping ───────────────────────────────────────────────


def map_error_to_exit_code(exc: BaseException) -> int:
    """Map a raised exception to the documented CLI exit code.

    Order: explicit error class → HTTP status_code → generic.
    """
    if isinstance(exc, AuthError):
        return EXIT_AUTH
    if isinstance(exc, ForbiddenError):
        # 403 is auth-adjacent; bias toward the AUTH code so an agent
        # knows to re-authenticate or escalate permission.
        return EXIT_AUTH
    if isinstance(exc, (NotFoundError, RegistryError)):
        return EXIT_NOT_FOUND
    if isinstance(exc, ValidationError):
        return EXIT_VALIDATION
    if isinstance(exc, ConfigError):
        return EXIT_USAGE
    if isinstance(exc, SafetyError):
        return EXIT_POLICY
    if isinstance(exc, BCLIError):
        # Fall back to HTTP-status-derived code (mirrors envelope wrap).
        return exit_code_for_status(getattr(exc, "status_code", None))
    return EXIT_GENERIC_ERROR


# ─── Remediation hint composition ────────────────────────────────────


def _login_hint(active_profile: str | None) -> str:
    if active_profile:
        return f"Run 'bcli auth login --profile {active_profile}' to re-authenticate."
    return "Run 'bcli auth login' to authenticate."


def _config_init_hint() -> str:
    return "Run 'bcli config init' to create a profile."


def _registry_import_hint() -> str:
    return (
        "Run 'bcli registry import --from-metadata <metadata-url>' "
        "or 'bcli registry import --from-postman <file.json>' "
        "to register the endpoint."
    )


def _did_you_mean_profiles(name: str, candidates: list[str]) -> str | None:
    matches = difflib.get_close_matches(name, candidates, n=3, cutoff=0.5)
    if not matches:
        return None
    return f"Did you mean: {', '.join(matches)}?"


def format_error_for_cli(
    exc: BaseException,
    *,
    active_profile: str | None,
    available_profiles: list[str] | None = None,
) -> str:
    """Compose the user-facing error message + remediation.

    The original exception message is kept verbatim; remediation is
    appended on a second line so log-tailers can grep for either.
    """
    base = str(exc)
    extras: list[str] = []

    if isinstance(exc, AuthError):
        if "bcli auth login" not in base:
            extras.append(_login_hint(active_profile))

    elif isinstance(exc, ConfigError):
        if "bcli config init" not in base:
            extras.append(_config_init_hint())
        if available_profiles and active_profile and active_profile not in available_profiles:
            hint = _did_you_mean_profiles(active_profile, available_profiles)
            if hint:
                extras.append(hint)

    elif isinstance(exc, RegistryError):
        # Raise site already injects "Did you mean: X, Y, Z?" when fuzzy
        # matches exist. Only suggest import when no such hint is there.
        if "Did you mean" not in base and "bcli registry import" not in base:
            extras.append(_registry_import_hint())

    if not extras:
        return base
    return base + "\n  " + "\n  ".join(extras)


__all__ = [
    "format_error_for_cli",
    "map_error_to_exit_code",
]
