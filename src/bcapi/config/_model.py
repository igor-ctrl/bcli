"""Pydantic models for bcapi configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field

from bcapi.config._defaults import DEFAULT_FORMAT, DEFAULT_PAGE_SIZE, DEFAULT_TIMEOUT


class BCProfile(BaseModel):
    """A named connection profile."""

    tenant_id: str
    environment: str
    company_id: str | None = None
    company_name: str | None = None

    # Auth
    auth_method: str = "client_credentials"
    client_id: str | None = None
    client_secret_env: str | None = None  # Name of env var holding the secret

    # Custom API defaults (for ad-hoc queries not in registry)
    api_publisher: str | None = None
    api_group: str | None = None
    api_version: str | None = None

    model_config = {"extra": "allow"}


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
            from bcapi.errors import ConfigError

            available = ", ".join(self.profiles.keys()) or "(none)"
            raise ConfigError(
                f"Profile '{profile_name}' not found. Available: {available}"
            )
        return self.profiles[profile_name]
