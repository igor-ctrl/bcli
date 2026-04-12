"""Endpoint registry for route resolution."""

from bcapi.registry._importers import import_from_json, import_from_postman
from bcapi.registry._registry import EndpointRegistry
from bcapi.registry._schema import EndpointMetadata

__all__ = [
    "EndpointMetadata",
    "EndpointRegistry",
    "import_from_json",
    "import_from_postman",
]
