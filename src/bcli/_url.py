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
        f"https://api.businesscentral.dynamics.com"
        f"/admin/v2.1/applications/businesscentral/environments"
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
