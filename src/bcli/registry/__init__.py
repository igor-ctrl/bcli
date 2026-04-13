"""Endpoint registry for route resolution."""

from bcli.registry._importers import import_from_json, import_from_postman
from bcli.registry._registry import EndpointRegistry
from bcli.registry._schema import EndpointMetadata

__all__ = [
    "EndpointMetadata",
    "EndpointRegistry",
    "import_from_json",
    "import_from_postman",
]
