"""AIP §Phase 4c — every error names the fix.

The CLI catches ``BCLIError`` subclasses at the outer ``main()`` boundary
and adds:

1. The right exit code (taxonomy from §Phase 4a).
2. A short, executable remediation hint after the error message.

The hint format is always ``Run 'bcli <subcommand> ...'.`` so an agent
can pattern-match the suggestion. ``RegistryError`` already carries
fuzzy "Did you mean: X, Y, Z?" suggestions; that path stays — we just
extend the recipe to ``AuthError``, ``ConfigError``, and the no-fuzzy-
match registry case.

These tests pin the *exit code* and the *suggestion text*. Hint placement
on stderr vs stdout is a detail the handler owns.
"""

from __future__ import annotations

from bcli.errors import AuthError, ConfigError, RegistryError
from bcli.exit_codes import EXIT_AUTH, EXIT_NOT_FOUND, EXIT_USAGE
from bcli_cli._error_handler import format_error_for_cli, map_error_to_exit_code


# ─── Exit-code mapping ───────────────────────────────────────────────


def test_auth_error_maps_to_exit_auth():
    assert map_error_to_exit_code(AuthError("token expired")) == EXIT_AUTH


def test_config_error_maps_to_exit_usage():
    """ConfigError is a setup/usage problem the user can fix locally."""
    assert map_error_to_exit_code(ConfigError("no profile")) == EXIT_USAGE


def test_registry_error_maps_to_exit_not_found():
    assert map_error_to_exit_code(RegistryError("foo not found")) == EXIT_NOT_FOUND


# ─── Remediation hint injection ──────────────────────────────────────


def test_auth_error_remediation_names_login_command():
    """AuthError messages get a `bcli auth login` hint appended."""
    msg = format_error_for_cli(AuthError("token expired"), active_profile="finance")
    assert "bcli auth login" in msg
    assert "--profile finance" in msg


def test_auth_error_without_profile_still_gets_generic_login_hint():
    msg = format_error_for_cli(AuthError("token expired"), active_profile=None)
    assert "bcli auth login" in msg


def test_config_error_when_no_profiles_suggests_config_init():
    """ConfigError messages from ``get_profile`` already mention init.

    The CLI handler is idempotent: if the upstream message already
    carries the hint, don't append a duplicate.
    """
    exc = ConfigError(
        "No profiles configured. Run 'bcli config init' to create your first profile."
    )
    msg = format_error_for_cli(exc, active_profile=None)
    # Hint appears exactly once.
    assert msg.count("bcli config init") == 1


def test_config_error_unknown_profile_gets_did_you_mean(monkeypatch):
    """Unknown profile name → suggest similar names + config init.

    We pass the candidate list explicitly so the handler doesn't need to
    re-load config (which the CLI already loaded once to fail).
    """
    exc = ConfigError("Profile 'finence' not found.")
    msg = format_error_for_cli(
        exc,
        active_profile="finence",
        available_profiles=["finance", "production", "sandbox"],
    )
    assert "Did you mean" in msg
    assert "finance" in msg


def test_registry_error_with_no_fuzzy_match_suggests_import():
    """RegistryError without ``Did you mean:`` → hint to import metadata.

    The fuzzy-match case is already handled at the raise site
    (``EndpointRegistry.resolve``); the handler only adds the import
    hint when no fuzzy candidates were found.
    """
    exc = RegistryError("Endpoint 'glargen' not found in any registry.")
    msg = format_error_for_cli(exc, active_profile="finance")
    assert "bcli registry import" in msg


def test_registry_error_with_fuzzy_match_preserves_hint():
    """If raise-site already has ``Did you mean:``, don't double-hint."""
    exc = RegistryError(
        "Endpoint 'vendor' not found in any registry. Did you mean: vendors?"
    )
    msg = format_error_for_cli(exc, active_profile="finance")
    assert "Did you mean: vendors" in msg
    # No duplicate import-hint when the fuzzy match already pointed somewhere.
    assert msg.count("bcli registry import") == 0


# ─── No-op for unknown error types ───────────────────────────────────


def test_unknown_exception_is_pass_through():
    """A non-BCLIError shouldn't be reformatted — just echoes the message."""
    msg = format_error_for_cli(RuntimeError("kaboom"), active_profile="finance")
    assert "kaboom" in msg
