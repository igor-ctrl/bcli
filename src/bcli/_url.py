"""URL builder for Business Central API endpoints."""

from __future__ import annotations

from bcli.config._defaults import BC_BASE_URL, BC_STANDARD_API_PATH


def build_url(
    *,
    environment: str,
    company_id: str,
    entity_set_name: str,
    record_id: str | None = None,
    # For custom APIs — None means standard v2.0
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
) -> str:
    """Build a full BC API URL.

    Standard v2.0:
        https://api.businesscentral.dynamics.com/v2.0/{env}/api/v2.0/companies({id})/{entity}

    Custom API:
        https://api.businesscentral.dynamics.com/v2.0/{env}/api/{pub}/{grp}/{ver}/companies({id})/{entity}
    """
    if publisher and group and version:
        api_path = f"api/{publisher}/{group}/{version}"
    else:
        api_path = BC_STANDARD_API_PATH

    url = f"{BC_BASE_URL}/{environment}/{api_path}/companies({company_id})/{entity_set_name}"

    if record_id:
        url = f"{url}({record_id})"

    return url


def build_companies_url(*, environment: str) -> str:
    """Build URL to list companies (no company context needed)."""
    return f"{BC_BASE_URL}/{environment}/{BC_STANDARD_API_PATH}/companies"


def build_environments_url(*, tenant_id: str) -> str:
    """Build URL for BC Admin Center environments API."""
    return (
        "https://api.businesscentral.dynamics.com"
        "/admin/v2.1/applications/businesscentral/environments"
    )


def build_metadata_url(
    *,
    environment: str,
    publisher: str | None = None,
    group: str | None = None,
    version: str | None = None,
) -> str:
    """Build URL for OData $metadata endpoint."""
    if publisher and group and version:
        api_path = f"api/{publisher}/{group}/{version}"
    else:
        api_path = BC_STANDARD_API_PATH
    return f"{BC_BASE_URL}/{environment}/{api_path}/$metadata"


# ─── Host allowlist for absolute URLs ─────────────────────────────────────
#
# bcli attaches a BC bearer token to every outgoing request. If a BC
# response ever returns an off-origin ``@odata.nextLink`` (e.g. a
# compromised custom-API page that returns ``@odata.nextLink:
# https://attacker.example/leak``), the paginator would happily follow it
# with the token attached, leaking the credential to the attacker. Guard
# the paginator (and any other absolute-URL follower) by running absolute
# URLs through ``assert_bc_origin`` before they reach the bearer-injecting
# transport.
#
# We allow the entire ``.businesscentral.dynamics.com`` suffix because BC
# is regional — ``api.businesscentral.dynamics.com`` for the API,
# ``api.bc.dynamics.com`` for the legacy alias, plus customer-specific
# regional CNAMEs that all live under the same parent suffix. The leading
# dot prevents the classic ``evilbusinesscentral.dynamics.com.attacker``
# trick.

# Suffix list is ordered most-specific-first; a hostname matches if it
# equals the suffix or ends with ``"." + suffix``.
_ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (
    "businesscentral.dynamics.com",
    "bc.dynamics.com",
)


def is_bc_origin(url: str) -> bool:
    """Return ``True`` if ``url`` is absolute and points at a BC host.

    Relative URLs (no scheme) are considered safe — they get joined to the
    SDK's BC base URL by httpx, so they can't leak auth elsewhere.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme:
        # Relative URL — caller will resolve it against the BC base URL.
        return True
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for suffix in _ALLOWED_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def assert_bc_origin(url: str) -> None:
    """Raise ``ValueError`` if ``url`` isn't a relative URL or BC host.

    Called by the transport layer before an absolute URL reaches the
    bearer-injecting request path. The error message intentionally
    surfaces the rejected URL so the operator can audit a misbehaving
    endpoint or registry entry.
    """
    if not is_bc_origin(url):
        raise ValueError(
            f"Refusing to attach BC credentials to off-origin URL: {url!r}. "
            f"Allowed host suffixes: {list(_ALLOWED_HOST_SUFFIXES)}. "
            f"This URL came from an @odata.nextLink or similar follow-up "
            f"reference; if the BC tenant is genuinely returning it, the "
            f"allowlist needs to be expanded — but check first that the "
            f"response wasn't tampered with."
        )
