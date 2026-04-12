"""Pydantic models for endpoint metadata."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EndpointMetadata(BaseModel):
    """Metadata for a single API endpoint."""

    entity_set_name: str
    entity_name: str = ""
    description: str = ""
    supports: list[str] = Field(default_factory=lambda: ["GET"])
    key_field: str = "id"
    category: str = ""

    # For standard v2.0 these are None (route is always /api/v2.0/)
    api_publisher: str | None = None
    api_group: str | None = None
    api_version: str | None = None

    # Optional metadata from imports
    source_table: str = ""
    page_number: str = ""
    editable: bool = False

    @property
    def is_custom(self) -> bool:
        """True if this is a custom API (not standard v2.0)."""
        return self.api_publisher is not None

    @property
    def route_display(self) -> str:
        """Human-readable route string."""
        if self.is_custom:
            return f"{self.api_publisher}/{self.api_group}/{self.api_version}"
        return "v2.0 (standard)"

    model_config = {"extra": "allow"}
