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

    # Company aliases: {"LLC": {"id": "REDACTED-...", "name": "Acme LLC"}, ...}
    companies: dict[str, CompanyAlias] = Field(default_factory=dict)

    # Auth
    auth_method: str = "client_credentials"
    client_id: str | None = None
    client_secret_env: str | None = None

    # Custom API defaults (for ad-hoc queries not in registry)
    api_publisher: str | None = None
    api_group: str | None = None
    api_version: str | None = None

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


class BCConfig(BaseModel):
    """Top-level configuration."""

    defaults: BCDefaults = Field(default_factory=BCDefaults)
    profiles: dict[str, BCProfile] = Field(default_factory=dict)

    def get_profile(self, name: str | None = None) -> BCProfile:
        """Get a profile by name, falling back to the default."""
        profile_name = name or self.defaults.profile
        if profile_name not in self.profiles:
            from bcli.errors import ConfigError

            available = ", ".join(self.profiles.keys()) or "(none)"
            raise ConfigError(
                f"Profile '{profile_name}' not found. Available: {available}"
            )
        return self.profiles[profile_name]
