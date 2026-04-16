"""Business Central ETL — dlt source + bcli bridge.

Two entry points:

- :func:`business_central` — generic dlt source for any BC tenant. Pass auth
  and an explicit entity list. No bcli coupling.

- :func:`bcli_profile` — bridge that reads entities from a bcli profile's
  registry and reuses bcli's authenticated session. Defaults match Fivetran
  behavior (multi-company, Fivetran audit columns).

Example — standalone:

    >>> from bcli.etl import business_central, EntityDef, fivetran_stamper
    >>> source = business_central(
    ...     tenant_id="...", client_id="...", client_secret="...",
    ...     environment="Production",
    ...     entities=[EntityDef(name="customers")],
    ...     multi_company=True,
    ...     stampers=[fivetran_stamper()],
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
from bcli.etl._stampers import (
    Stamper,
    audit_stamper,
    company_id_stamper,
    fivetran_stamper,
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
    "fivetran_stamper",
    "audit_stamper",
    "company_id_stamper",
    # bcli bridge
    "bcli_profile",
    "load_entities_from_bcli_registry",
]
