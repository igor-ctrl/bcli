"""Shared CLI state — resolved config, lazy client initialization."""

from __future__ import annotations

from dataclasses import dataclass, field

from bcli.config import BCConfig, BCProfile, load_config
from bcli.registry._registry import EndpointRegistry


@dataclass
class CLIState:
    """Mutable state shared across CLI commands."""

    # Set by global callback
    profile_name: str | None = None
    env_override: str | None = None
    company_override: str | None = None
    format: str = "table"
    verbose: bool = False
    debug: bool = False
    dry_run: bool = False
    quiet: bool = False

    # Resolved lazily
    _config: BCConfig | None = field(default=None, repr=False)
    _registry: EndpointRegistry | None = field(default=None, repr=False)

    @property
    def config(self) -> BCConfig:
        if self._config is None:
            self._config = load_config()
        return self._config

    @config.setter
    def config(self, value: BCConfig) -> None:
        self._config = value

    @property
    def profile(self) -> BCProfile:
        p = self.config.get_profile(self.profile_name)
        # Apply per-command overrides
        if self.env_override:
            p = p.model_copy(update={"environment": self.env_override})
        if self.company_override and self.company_override.lower() != "all":
            # Resolve alias to company_id
            try:
                resolved_id, resolved_name = p.resolve_company(self.company_override)
                p = p.model_copy(update={
                    "company_id": resolved_id,
                    "company_name": resolved_name or self.company_override,
                })
            except ValueError:
                pass  # "all" — handled by get_cmd
        return p

    @property
    def registry(self) -> EndpointRegistry:
        if self._registry is None:
            name = self.profile_name or self.config.defaults.profile
            profile = self.config.get_profile(self.profile_name)
            self._registry = EndpointRegistry(
                profile_name=name,
                disable_standard=profile.disable_standard_api,
                allowed_categories=profile.allowed_categories or None,
                allowed_endpoints=profile.allowed_endpoints or None,
            )
        return self._registry

    @property
    def active_profile_name(self) -> str:
        return self.profile_name or self.config.defaults.profile


# Singleton
state = CLIState()
