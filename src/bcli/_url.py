"""URL builder for Business Central API endpoints."""

from __future__ import annotations

from bcli.config._defaults import BC_BASE_URL, BC_STANDARD_API_PATH


def _validate_route_segment(name: str, value: str) -> None:
    """Validate a custom-API route segment for path-traversal attacks.

    Raises ValueError if the segment is empty, contains path separators,
    or contains path-traversal sequences (. or ..).
    """
    if not value or not value.strip():
        raise ValueError(f"Invalid custom-API route segment {name!r}: must not be empty.")

    # Reject segments containing path separators or path traversal
    if "/" in value or "\\" in value:
        raise ValueError(
            f"Invalid custom-API route segment {name!r} = {value!r}: must not contain '/' or '\\'."
        )

    # Reject . and .. segments, or any segment containing them as a complete part
    parts = value.split("/")  # Already checked above, but defense in depth
    for part in parts:
        if part in (".", ".."):
            raise ValueError(
                f"Invalid custom-API route segment {name!r} = {value!r}: "
                f"must not contain '.' or '..' path-traversal sequences."
            )


#: Characters that let a value escape the URL component it is spliced into.
#: A record key legitimately contains quotes, commas, equals signs, hyphens and
#: even parentheses inside a quoted string (``'ACME (US)'``) — none of which can
#: start a new path segment. These four can.
_URL_STRUCTURE_CHARS = ("/", "\\", "?", "#")


def validate_record_key(name: str, value: str) -> None:
    """Validate an OData key or entity-set name as a single URL path component.

    ``entity_set_name`` and ``record_id`` are spliced straight into the path, and
    a key is caller-influenced anywhere one is accepted from outside. A raw ``/``
    starts a new path segment, so ``foo(1)/../../bar('X'`` composes a URL that
    addresses ``bar`` while every earlier check only ever saw ``foo`` — including
    the endpoint-registry lookup and the ``disable_standard_api`` gate, both of
    which key on the entity-set name alone.

    Raises ValueError on empty input, raw path/query delimiters, and the
    ``.``/``..`` traversal segments.
    """
    if not value or not value.strip():
        raise ValueError(f"Invalid {name}: must not be empty.")

    for char in _URL_STRUCTURE_CHARS:
        if char in value:
            raise ValueError(
                f"Invalid {name} {value!r}: must not contain {char!r}. A key is a "
                f"single URL path component — percent-encode the character if it "
                f"is genuinely part of the key."
            )

    if value.strip() in (".", ".."):
        raise ValueError(
            f"Invalid {name} {value!r}: '.' and '..' are path-traversal segments."
        )


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
    validate_record_key("entity_set_name", entity_set_name)
    # `None` means "address the collection" and is a supported call — `bcli get
    # <entity>` with no id reads the set. An *empty* key is not the same thing:
    # it means the caller meant one record and supplied nothing, and silently
    # dropping it would retarget the request at the whole collection (a DELETE
    # or PATCH against an entity set rather than a row). Validate anything that
    # was actually passed, including "".
    if record_id is not None:
        validate_record_key("record_id", record_id)

    if publisher and group and version:
        _validate_route_segment("publisher", publisher)
        _validate_route_segment("group", group)
        _validate_route_segment("version", version)
        api_path = f"api/{publisher}/{group}/{version}"
    else:
        api_path = BC_STANDARD_API_PATH

    url = f"{BC_BASE_URL}/{environment}/{api_path}/companies({company_id})/{entity_set_name}"

    if record_id is not None:
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
        _validate_route_segment("publisher", publisher)
        _validate_route_segment("group", group)
        _validate_route_segment("version", version)
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
    if parsed.scheme != "https":
        # A bearer token must never ride a cleartext request. BC always serves
        # https, so an absolute http:// URL is tampering or misconfiguration —
        # refuse it before the token is attached.
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
