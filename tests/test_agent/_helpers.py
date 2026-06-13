"""Shared fakes for the agent engine tests — no network, no real client.

Imported as a sibling module (the test dir has no ``__init__.py``, so
pytest's default prepend import mode puts it on ``sys.path``).
"""

from __future__ import annotations

from typing import Any

from bcli.agent._runtime import AgentRuntime


class FakeProfile:
    """Minimal stand-in for :class:`bcli.config._model.BCProfile`."""

    def __init__(
        self,
        *,
        environment: str = "sandbox",
        disable_writes: bool = False,
        company_id: str = "company-1",
        company_name: str = "Test Co",
        auth_method: str = "client_credentials",
    ) -> None:
        self.environment = environment
        self.disable_writes = disable_writes
        self.company_id = company_id
        self.company_name = company_name
        self.auth_method = auth_method
        self.companies: dict[str, Any] = {}
        self.allowed_categories: list[str] = []
        self.disable_standard_api = False

    def resolve_company(self, alias_or_id: str | None = None):
        if alias_or_id and alias_or_id.lower() == "all":
            raise ValueError("all")
        return self.company_id, self.company_name


class FakeMeta:
    """Stand-in for an EndpointMetadata record."""

    def __init__(self, name: str, *, caution: str = "low", domain: str = "standard"):
        self.entity_set_name = name
        self.description = f"{name} endpoint"
        self.supports = ["GET"]
        self.domain = domain
        self.caution = caution
        self.key_field = "id"
        self.is_custom = False
        self.field_names: list[str] = []
        self.api_publisher = None
        self.api_group = None
        self.api_version = None


class FakeRegistry:
    """Stand-in registry returning canned metadata."""

    def __init__(self, metas: dict[str, FakeMeta] | None = None):
        self._metas = metas or {}

    def get(self, name: str) -> FakeMeta | None:
        return self._metas.get(name)

    def resolve(self, name: str) -> FakeMeta:
        if name in self._metas:
            return self._metas[name]
        from bcli.errors import RegistryError

        raise RegistryError(f"Unknown endpoint '{name}'.")

    def search(self, pattern: str) -> list[FakeMeta]:
        return [m for n, m in self._metas.items() if pattern.lower() in n.lower()]

    def list_all(self, **_kw) -> list[FakeMeta]:
        return list(self._metas.values())


def make_runtime(
    *,
    profile: FakeProfile | None = None,
    registry: FakeRegistry | None = None,
    plan_mode: bool = False,
    auto_approve: bool = False,
) -> AgentRuntime:
    return AgentRuntime(
        client=None,  # type: ignore[arg-type] — handlers under test never hit it
        profile=profile or FakeProfile(),  # type: ignore[arg-type]
        profile_name="test",
        registry=registry,  # type: ignore[arg-type]
        plan_mode=plan_mode,
        auto_approve=auto_approve,
    )
