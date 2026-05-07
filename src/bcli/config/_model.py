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

    # Write safety. When True, AsyncBCClient.post / patch / delete raise
    # SafetyError client-side regardless of what the user types. Useful
    # for read-only profiles (e.g. domain teams who consume but never
    # mutate). The actual security boundary remains the BC permission
    # set; this is a UX guardrail to prevent accidents.
    disable_writes: bool = False

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


class TelemetryConfig(BaseModel):
    """Optional usage-telemetry sink for bcli — plug-and-play backend.

    Built-in backends:
      * ``"null"``           — drop everything (default; effectively disabled)
      * ``"console"``        — pretty-print events to stderr (handy for dev)
      * ``"azure_monitor"``  — Azure Application Insights via
                               ``azure-monitor-opentelemetry`` (extra: ``[telemetry]``)

    Custom backends:
      * ``"my_pkg.module:MySink"`` — any importable class implementing the
        :class:`bcli.telemetry.TelemetrySink` protocol. Useful for AWS
        CloudWatch, Datadog, Honeycomb, or an internal HTTP webhook.

    The class must expose a ``from_config(cls, config: TelemetryConfig)``
    classmethod that builds an instance.

    Privacy defaults are conservative: nothing leaves the laptop unless
    ``enabled = true`` *and* the resolved backend is non-null. Even then,
    full filter text and signed-in UPN are dropped unless explicitly
    opted in.
    """

    enabled: bool = False
    backend: str = "null"
    connection_string: str = ""
    capture_filter_text: bool = False
    capture_user_upn: bool = False
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    model_config = {"extra": "allow"}

    @property
    def is_active(self) -> bool:
        """True iff opted-in and a non-null backend is selected."""
        return self.enabled and self.backend.strip().lower() not in ("", "null")


class AuditConfig(BaseModel):
    """Optional audit log for write operations.

    When ``enabled = true`` every CLI write (POST / PATCH / DELETE / attach
    upload) appends one JSONL line to a per-profile log file. Captures
    request shape, resolved URL, response status, BC correlation id,
    latency, and outcome — sufficient for forensic review and for agent-
    driven workflows where you want a paper trail of what was done.

    Defaults are off and conservative: zero overhead when disabled, no
    capture of read traffic, and request bodies are key-redacted before
    write.

    Built-in backends:

    * ``"null"``  — drop everything (effectively disabled).
    * ``"jsonl"`` — append one JSON object per line to ``path`` (default).

    The path supports a ``{profile}`` placeholder so a single global
    config produces a per-profile log file automatically.
    """

    enabled: bool = False
    backend: str = "jsonl"
    path: str | None = None
    max_size_mb: int = Field(default=50, ge=1)
    include_reads: bool = False
    redact_keys: list[str] = Field(
        default_factory=lambda: [
            "password",
            "secret",
            "token",
            "key",
            "apiKey",
            "authorization",
        ]
    )

    model_config = {"extra": "allow"}


class BCConfig(BaseModel):
    """Top-level configuration."""

    defaults: BCDefaults = Field(default_factory=BCDefaults)
    profiles: dict[str, BCProfile] = Field(default_factory=dict)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)

    model_config = {"extra": "allow"}

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
