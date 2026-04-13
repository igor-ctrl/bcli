"""Write safety gate for Business Central operations.

SafeContext ensures every write operation explicitly specifies the target
environment and company, preventing wrong-environment writes. Production
writes require additional confirmation.

Domain rules control per-domain write behavior:
- "finance" endpoints default to draft status on create
- All other domains allow writes without draft enforcement

Domain rules are configurable — pass custom rules to SafeContext or
override the defaults for your organization's needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bcli.errors import SafetyError


@dataclass(frozen=True)
class DomainRule:
    """Write safety rule for an endpoint domain."""

    allow_write: bool = True
    require_draft: bool = False
    require_production_confirm: bool = True


# Default domain rules — override with SafeContext(domain_rules={...})
DEFAULT_DOMAIN_RULES: dict[str, DomainRule] = {
    "finance": DomainRule(require_draft=True, require_production_confirm=True),
    "technical": DomainRule(require_draft=False, require_production_confirm=True),
    "standard": DomainRule(require_draft=False, require_production_confirm=True),
}


class SafeContext:
    """Async context manager that gates write operations with safety checks.

    Usage:
        async with SafeContext(
            client=client,
            environment="Sandbox",
            company_id="c-123",
        ) as sw:
            await sw.post("salesInvoices", body={...})

    For production environments:
        async with SafeContext(
            client=client,
            environment="Production",
            company_id="c-123",
            confirm_production=True,
        ) as sw:
            await sw.post("items", body={...})
    """

    def __init__(
        self,
        *,
        client: Any,
        environment: str,
        company_id: str,
        confirm_production: bool = False,
        domain_rules: dict[str, DomainRule] | None = None,
    ) -> None:
        if not environment:
            raise SafetyError("SafeContext requires an explicit environment.")
        if not company_id:
            raise SafetyError("SafeContext requires an explicit company_id.")

        is_production = environment.lower() in ("production", "prod")
        if is_production and not confirm_production:
            raise SafetyError(
                f"Production writes to '{environment}' require confirm_production=True. "
                "This is a safety gate to prevent accidental production modifications."
            )

        self._client = client
        self._environment = environment
        self._company_id = company_id
        self._domain_rules = domain_rules or DEFAULT_DOMAIN_RULES

    async def __aenter__(self) -> SafeContext:
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass

    def _get_rule(self, domain: str) -> DomainRule:
        """Get the domain rule, falling back to standard defaults."""
        return self._domain_rules.get(domain, DomainRule())

    def _apply_draft_if_needed(
        self, body: dict[str, Any], domain: str,
    ) -> dict[str, Any]:
        """If the domain requires draft, set status to Draft unless already set."""
        rule = self._get_rule(domain)
        if rule.require_draft and "status" not in body:
            return {**body, "status": "Draft"}
        return body

    async def post(
        self,
        entity_set_name: str,
        body: dict[str, Any],
        *,
        domain: str = "standard",
        publisher: str | None = None,
        group: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        """POST (create) with safety checks."""
        rule = self._get_rule(domain)
        if not rule.allow_write:
            raise SafetyError(
                f"Writes are not allowed for domain '{domain}'."
            )
        safe_body = self._apply_draft_if_needed(body, domain)
        return await self._client.post(
            entity_set_name, safe_body,
            publisher=publisher, group=group, version=version,
        )

    async def patch(
        self,
        entity_set_name: str,
        record_id: str,
        body: dict[str, Any],
        *,
        domain: str = "standard",
        etag: str = "*",
        publisher: str | None = None,
        group: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        """PATCH (update) with safety checks."""
        rule = self._get_rule(domain)
        if not rule.allow_write:
            raise SafetyError(
                f"Writes are not allowed for domain '{domain}'."
            )
        return await self._client.patch(
            entity_set_name, record_id, body,
            etag=etag, publisher=publisher, group=group, version=version,
        )

    async def delete(
        self,
        entity_set_name: str,
        record_id: str,
        *,
        domain: str = "standard",
        etag: str = "*",
        publisher: str | None = None,
        group: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        """DELETE with safety checks."""
        rule = self._get_rule(domain)
        if not rule.allow_write:
            raise SafetyError(
                f"Writes are not allowed for domain '{domain}'."
            )
        return await self._client.delete(
            entity_set_name, record_id,
            etag=etag, publisher=publisher, group=group, version=version,
        )

    @property
    def environment(self) -> str:
        return self._environment

    @property
    def company_id(self) -> str:
        return self._company_id
