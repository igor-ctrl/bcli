"""bcli — Python SDK for Microsoft Dynamics 365 Business Central APIs."""

from bcli._version import __version__
from bcli.client import AsyncBCClient, BCClient
from bcli.client._safety import DomainRule, SafeContext
from bcli.config import BCConfig, load_config
from bcli.errors import (
    AuthError,
    BCLIError,
    ConfigError,
    ForbiddenError,
    NotFoundError,
    RegistryError,
    SafetyError,
    ServerError,
    ThrottledError,
    ValidationError,
)
from bcli.odata import Query
from bcli.registry import EndpointRegistry

__all__ = [
    "__version__",
    "AsyncBCClient",
    "AuthError",
    "BCLIError",
    "BCClient",
    "BCConfig",
    "ConfigError",
    "DomainRule",
    "EndpointRegistry",
    "ForbiddenError",
    "NotFoundError",
    "Query",
    "RegistryError",
    "SafeContext",
    "SafetyError",
    "ServerError",
    "ThrottledError",
    "ValidationError",
    "load_config",
]
