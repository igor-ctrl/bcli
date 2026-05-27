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

    # Company aliases: {"US": {"id": "f99bd320-...", "name": "Contoso Ltd"}, ...}
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


class ExtractConfig(BaseModel):
    """Optional PDF → structured-data extraction backend for ``bcli extract``.

    Mirrors the pluggable shape of :class:`TelemetryConfig`. Built-in backends:

    * ``"null"``    — no extraction; ``bcli extract`` errors with guidance.
    * ``"claude"``  — Anthropic Claude vision API (requires ``[extract-claude]``
                      and ``ANTHROPIC_API_KEY``).
    * ``"openai"``  — OpenAI vision API (requires ``[extract-openai]`` and
                      ``OPENAI_API_KEY``).

    Custom backends:
      * ``"my_pkg.module:MyExtractor"`` — any importable class implementing the
        :class:`bcli.extract.ExtractorBackend` protocol. Useful for AWS
        Textract, Firecrawl, OpenDataLoader, or a self-hosted vision model.

    The class must expose a ``from_config(cls, config: ExtractConfig)``
    classmethod that builds an instance.

    ``model`` and ``api_key_env`` default to backend-appropriate values when
    left blank, so most users only need to set ``backend`` (e.g.
    ``backend = "openai"`` picks ``gpt-5`` and ``OPENAI_API_KEY``). Override
    when you want a non-default model or a custom env-var name.

    High-stakes-data note: this layer extracts; it does **not** post to
    BC. The CLI emits a batch.yaml + ``.extracted.json`` traceability sidecar
    so a human reviews per-field source pages against the PDF before the
    batch runner mutates anything — required workflow for regulated
    records, financial postings, or any data with real-world blast radius.
    """

    backend: str = "null"
    model: str = ""               # backend-specific default applied at from_config
    api_key_env: str = ""         # backend-specific default applied at from_config
    schemas_dir: str | None = None  # ~/.config/bcli/extract/schemas/ if None
    # Anthropic per-document limits at time of writing. OpenAI's vary by
    # model + Files API path; the backend overrides these when needed.
    max_pdf_bytes: int = 32 * 1024 * 1024
    max_pdf_pages: int = 100
    max_output_tokens: int = 8000
    # OpenAI-specific overrides (ignored by other backends).
    openai_base_url: str | None = None
    openai_organization: str | None = None

    model_config = {"extra": "allow", "protected_namespaces": ()}


class AskConfig(BaseModel):
    """Settings for ``bcli ask`` — the oracle / second-opinion command.

    Mirrors the pluggable shape of :class:`ExtractConfig`. Built-in
    backends:

    * ``"null"``   — no backend; ``bcli ask`` errors with guidance.
    * ``"claude"`` — Anthropic Claude (requires ``[ask-claude]`` and
                     ``ANTHROPIC_API_KEY``).
    * ``"openai"`` — OpenAI Responses API (requires ``[ask-openai]``
                     and ``OPENAI_API_KEY``).

    Third-party backends:
      * ``"my_pkg.module:MyAsker"`` — any importable class
        implementing :class:`bcli.ask.AskBackend`.

    ``context_providers`` lists the entry-point names registered under
    ``bcli.ask.context_providers`` the user opted into. Pack
    recommendations surface as hints during ``bcli pack install`` but
    are never auto-enabled — this list is the binding decision (R8).
    """

    backend: str = "null"
    model: str = ""
    api_key_env: str = ""
    max_tokens: int = Field(default=1024, ge=128, le=32768)
    include_describe: bool = True
    include_http_tail: bool = True
    context_providers: list[str] = Field(default_factory=list)
    base_url: str | None = None
    organization: str | None = None

    model_config = {"extra": "allow", "protected_namespaces": ()}


class ContextConfig(BaseModel):
    """LLM-context layer settings — drives :mod:`bcli.context`.

    The context layer feeds future LLM-driven features (``bcli ask``,
    ``bcli agent``) with a typed, redacted bundle of last-error + recent
    HTTP + profile + describe excerpt. All knobs default to the most
    private setting; users opt in per knob.

    * ``tail`` — when ``True`` a rotating NDJSON handler attaches to the
      ``bcli.http`` logger so the most recent ~200 requests land on disk
      for ``bcli ask`` to read.
    * ``redact_company_ids`` — when ``True`` the bundler scrubs GUID-
      shaped substrings (BC company ids, record systemIds) before
      shipping context to a model. Useful when the operator wants the
      help but not the identifiers.
    * ``attachment_max_bytes`` — per-attachment byte cap applied
      *after* redaction. Default 256 KiB matches the conservative side
      of LLM context cost vs information density.
    """

    tail: bool = False
    redact_company_ids: bool = False
    attachment_max_bytes: int = Field(default=256 * 1024, ge=1024)

    model_config = {"extra": "allow"}


class EtlConfig(BaseModel):
    """Settings for ``bcli etl`` — the dlt-based extract pipeline.

    ``stampers`` lists entry-point names registered under the
    ``bcli.etl.stampers`` group that should post-process every page of
    records before dlt ingests them (sync timestamps, soft-delete flags,
    vendor-specific audit columns, …). Applied in the order given.

    The package ships no audit-column stampers; the list is empty by
    default so output stays a clean record shape. A downstream package
    registers a stamper under the ``bcli.etl.stampers`` group and the
    operator opts in by name, e.g. ``stampers = ["audit"]``. Unknown
    names are skipped with a warning — see
    :mod:`bcli.etl._stamper_factory`.
    """

    stampers: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class BCConfig(BaseModel):
    """Top-level configuration."""

    defaults: BCDefaults = Field(default_factory=BCDefaults)
    profiles: dict[str, BCProfile] = Field(default_factory=dict)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    extract: ExtractConfig = Field(default_factory=ExtractConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    ask: AskConfig = Field(default_factory=AskConfig)
    etl: EtlConfig = Field(default_factory=EtlConfig)

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
