"""Tests for the BC-origin allowlist applied to absolute URLs.

A malicious BC response (or compromised custom-API endpoint) returning an
off-origin ``@odata.nextLink`` could otherwise leak the bearer token to
the attacker. ``assert_bc_origin`` rejects such URLs before the bearer is
attached.
"""

from __future__ import annotations

import pytest

from bcli._url import assert_bc_origin, is_bc_origin


# ── Allowed origins ───────────────────────────────────────────────────────


# Real BC URL shape:
#   https://api.businesscentral.dynamics.com/v2.0/{environment}/{api_path}/companies({id})/{entity}
# where {api_path} is "api/v2.0" for the standard catalogue or
# "api/{publisher}/{group}/{version}" for custom APIs. Tests cover that
# realistic shape so a future reader doesn't think bcli builds URLs
# differently.
_COMPANY_ID = "00000000-0000-0000-0000-000000000000"


@pytest.mark.parametrize("url", [
    f"https://api.businesscentral.dynamics.com/v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers",
    f"https://api.businesscentral.dynamics.com/v2.0/SandboxFeb26/api/acme/finance/v1.5/companies({_COMPANY_ID})/vendors",
    # Regional / sub-domain variants
    f"https://eu.api.businesscentral.dynamics.com/v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers",
    f"https://api.bc.dynamics.com/v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers",
    # Relative URLs are fine — caller resolves them against the BC base.
    f"/v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers",
    f"v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers",
])
def test_is_bc_origin_accepts_legitimate_urls(url):
    assert is_bc_origin(url) is True
    assert_bc_origin(url)  # should not raise


# ── Rejected origins (the actual security boundary) ──────────────────────


@pytest.mark.parametrize("url,reason", [
    # The classic suffix-trick: appending the legitimate suffix as a path
    # component to a different domain.
    ("https://attacker.example/businesscentral.dynamics.com/leak", "different host"),
    # Confusable hostname trying to look like a BC sub-domain.
    ("https://evilbusinesscentral.dynamics.com.attacker.example/leak",
     "evil prefix not under the BC suffix"),
    ("https://businesscentral.dynamics.com.attacker.example/leak",
     "the BC suffix appears in the path of a different domain"),
    # Off-origin entirely.
    ("https://attacker.example/v2.0/whatever", "different domain"),
    ("https://localhost:1234/leak", "localhost is not a BC host"),
    ("http://attacker.example/v2.0/whatever", "wrong scheme + wrong host"),
    # Right host, wrong scheme: a bearer token must never ride cleartext.
    (f"http://api.businesscentral.dynamics.com/v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers",
     "http scheme rejected even for an allowed BC host"),
    # Non-HTTP schemes shouldn't slip through.
    ("file:///etc/passwd", "file scheme rejected"),
    ("javascript:alert(1)", "non-http scheme rejected"),
    # Empty / malformed URLs.
    ("https://", "no host"),
])
def test_is_bc_origin_rejects_malicious_urls(url, reason):
    assert is_bc_origin(url) is False, f"should reject ({reason}): {url}"
    with pytest.raises(ValueError, match="off-origin|no host|non-HTTP"):
        assert_bc_origin(url)


def test_https_required_for_bc_host():
    """The same BC URL is accepted over https and refused over http — a
    bearer token must never be attached to a cleartext request (#21 review)."""
    https_url = (
        f"https://api.businesscentral.dynamics.com/v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers"
    )
    http_url = https_url.replace("https://", "http://", 1)
    assert is_bc_origin(https_url) is True
    assert is_bc_origin(http_url) is False
    with pytest.raises(ValueError, match="off-origin"):
        assert_bc_origin(http_url)


def test_assert_message_includes_rejected_url():
    """The error explicitly names the URL so operators can audit it."""
    with pytest.raises(ValueError, match="attacker.example"):
        assert_bc_origin("https://attacker.example/foo")


def test_case_insensitive_host_check():
    """Hostname compare is lowercased; uppercase variants still pass."""
    assert is_bc_origin(
        f"https://API.BusinessCentral.Dynamics.com/v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers"
    ) is True


# ── ETL module's inline copy stays in sync ───────────────────────────────


def test_etl_inline_check_matches_canonical():
    """The duplicated check in bcli.etl._client must reject the same URLs.

    The ETL layer keeps an inline copy because ``bcli.etl.*`` deliberately
    avoids importing from outer ``bcli.*`` modules. Skipped when the
    optional ``dlt`` extra isn't installed (matches the rest of
    ``tests/test_etl/``).
    """
    pytest.importorskip("dlt")
    from bcli.etl._client import _assert_bc_origin as etl_assert

    # Allowed
    etl_assert(
        f"https://api.businesscentral.dynamics.com/v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers"
    )
    etl_assert(f"/v2.0/Production/api/v2.0/companies({_COMPANY_ID})/customers")

    # Rejected
    for bad in [
        "https://attacker.example/leak",
        "https://evilbusinesscentral.dynamics.com.attacker.example/leak",
        "file:///etc/passwd",
        # https-only: cleartext to a BC host is still refused.
        "http://api.businesscentral.dynamics.com/leak",
    ]:
        with pytest.raises(ValueError):
            etl_assert(bad)
