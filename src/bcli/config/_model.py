"""Pydantic models for bcli configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field

from bcli.config._defaults import DEFAULT_FORMAT, DEFAULT_PAGE_SIZE, DEFAULT_TIMEOUT


class CompanyAlias(BaseModel):
    """A named alias for a company (entity)."""

    id: str
    name: str = ""


class BCProfile(BaseModel):
    """A named connection profile."""

    tenant_id: str
    environment: str
    company_id: str | None = None
    company_name: str | None = None

    # Company aliases: {"US": {"id": "REDACTED-...", "name": "Contoso Ltd"}, ...}
    companies: dict[str, CompanyAlias] = Field(default_factory=dict)

    # Auth
    auth_method: str = "client_credentials"
    client_id: str | None = None
    client_secret_env: str | None = None

    # Custom API defaults (for ad-hoc queries not in registry)
    api_publisher: str | None = None
    api_group: str | None = None
    api_version: str | None = None

    # Registry control
    disable_standard_api: bool = False
    allowed_categories: list[str] = Field(default_factory=list)
    allowed_endpoints: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}

    def resolve_company(self, alias_or_id: str | None = None) -> tuple[str, str | None]:
        """Resolve a company alias or ID to (company_id, display_name).

        Returns the company_id and an optional display name.
        - None → default company_id
        - "all" → raises ValueError (caller must handle iteration)
        - "LLC" → looks up in companies dict
        - GUID → used directly
        """
        if alias_or_id is None:
            if not self.company_id:
                from bcli.errors import ConfigError

                raise ConfigError(
                    "No company_id configured. Run 'bcli company list' and 'bcli company use <id>'."
                )
            return self.company_id, self.company_name

        if alias_or_id.lower() == "all":
            raise ValueError("all")

        # Check aliases
        if alias_or_id in self.companies:
            alias = self.companies[alias_or_id]
            return alias.id, alias.name or alias_or_id

        # Check if it's already a GUID-like string
        if len(alias_or_id) > 8 and "-" in alias_or_id:
            return alias_or_id, None

        # Try case-insensitive alias lookup
        for key, alias in self.companies.items():
            if key.lower() == alias_or_id.lower():
                return alias.id, alias.name or key

        from bcli.errors import ConfigError

        available = ", ".join(self.companies.keys()) if self.companies else "(none)"
        raise ConfigError(
            f"Company alias '{alias_or_id}' not found. Available: {available}. "
            "Use 'bcli company alias <name> <id>' to create one."
        )

    def all_companies(self) -> list[tuple[str, str, str]]:
        """Return all known companies as [(alias, id, name), ...]."""
        results = []
        for alias, company in self.companies.items():
            results.append((alias, company.id, company.name))
        # Include the default company if not already in aliases
        if self.company_id:
            alias_ids = {c.id for c in self.companies.values()}
            if self.company_id not in alias_ids:
                results.append(("default", self.company_id, self.company_name or ""))
        return results


class BCDefaults(BaseModel):
    """Global defaults."""

    profile: str = "default"
    format: str = DEFAULT_FORMAT
    page_size: int = DEFAULT_PAGE_SIZE
    timeout: int = DEFAULT_TIMEOUT

    model_config = {"extra": "allow"}


class WorkOSRoleMapping(BaseModel):
    """Maps a WorkOS role slug to a BC Entra app client_id."""

    roles: list[str]
    bc_client_id: str


class WorkOSConfig(BaseModel):
    """WorkOS AuthKit configuration for role-based BC access."""

    api_key: str = ""
    client_id: str = ""
    groups: dict[str, WorkOSRoleMapping] = Field(default_factory=dict)

    def get_role_mapping(self) -> dict[str, str]:
        """Build a flat {role_slug: bc_client_id} mapping."""
        mapping: dict[str, str] = {}
        for group in self.groups.values():
            for role in group.roles:
                mapping[role] = group.bc_client_id
        return mapping

    model_config = {"extra": "allow"}


class TelemetryConfig(BaseModel):
    """Optional usage-telemetry sink for bcli (Azure Application Insights).

    When ``enabled`` is True and ``connection_string`` is set, bcli emits
    structured events for command runs, queries, auth, and errors. All
    events flow through :mod:`bcli.telemetry`, which redacts secrets and
    omits filter strings + user identity unless explicitly opted in.

    Privacy defaults are conservative: nothing leaves the laptop unless
    ``enabled = true`` *and* ``connection_string`` resolves. Even then,
    full filter text and signed-in UPN are dropped by default.
    """

    enabled: bool = False
    connection_string: str = ""
    capture_filter_text: bool = False
    capture_user_upn: bool = False
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    model_config = {"extra": "allow"}

    @property
    def is_active(self) -> bool:
        """True only when both opt-in flag and a connection string are set."""
        return self.enabled and bool(self.connection_string.strip())


class BCConfig(BaseModel):
    """Top-level configuration."""

    defaults: BCDefaults = Field(default_factory=BCDefaults)
    profiles: dict[str, BCProfile] = Field(default_factory=dict)
    workos: WorkOSConfig = Field(default_factory=WorkOSConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)

    def get_profile(self, name: str | None = None) -> BCProfile:
        """Get a profile by name, falling back to the default."""
        profile_name = name or self.defaults.profile
        if profile_name not in self.profiles:
            from bcli.errors import ConfigError

            if not self.profiles:
                raise ConfigError(
                    "No profiles configured. Run 'bcli config init' to create your first profile."
                )
            available = ", ".join(self.profiles.keys())
            raise ConfigError(
                f"Profile '{profile_name}' not found. Available: {available}. "
                f"Run 'bcli config init --profile {profile_name}' to create it,"
                f" or 'bcli config use <name>' to switch."
            )
        return self.profiles[profile_name]
