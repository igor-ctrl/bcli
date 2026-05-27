"""Business Central ETL — dlt source + bcli bridge.

Two entry points:

- :func:`business_central` — generic dlt source for any BC tenant. Pass auth
  and an explicit entity list. No bcli coupling.

- :func:`bcli_profile` — bridge that reads entities from a bcli profile's
  registry and reuses bcli's authenticated session. Multi-company on by
  default; audit columns are opt-in via the ``bcli.etl.stampers``
  entry-point group + ``[etl] stampers`` config (vendor-neutral by default).

Example — standalone:

    >>> from bcli.etl import business_central, EntityDef, audit_stamper
    >>> source = business_central(
    ...     tenant_id="...", client_id="...", client_secret="...",
    ...     environment="Production",
    ...     entities=[EntityDef(name="customers")],
    ...     multi_company=True,
    ...     stampers=[audit_stamper("bc-prod")],
    ... )

Example — bcli bridge:

    >>> from bcli.etl import bcli_profile
    >>> source = bcli_profile(profile="prod")
"""

from bcli.etl._auth import (
    AuthProvider,
    ClientCredentialsAuth,
    StaticTokenAuth,
)
from bcli.etl._bridge import bcli_profile, load_entities_from_bcli_registry
from bcli.etl._generic import EntityDef, business_central
from bcli.etl._polaris import PolarisConfig, register_load_with_polaris
from bcli.etl._stamper_factory import build_stampers, discover_stamper_factories
from bcli.etl._stampers import (
    Stamper,
    audit_stamper,
    company_id_stamper,
)

__all__ = [
    # Generic source
    "business_central",
    "EntityDef",
    # Auth
    "AuthProvider",
    "ClientCredentialsAuth",
    "StaticTokenAuth",
    # Stampers
    "Stamper",
    "audit_stamper",
    "company_id_stamper",
    # Stamper plugin discovery
    "build_stampers",
    "discover_stamper_factories",
    # bcli bridge
    "bcli_profile",
    "load_entities_from_bcli_registry",
    # Polaris integration
    "PolarisConfig",
    "register_load_with_polaris",
]
