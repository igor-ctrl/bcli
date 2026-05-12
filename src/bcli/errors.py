"""Exception hierarchy for bcli."""

from __future__ import annotations


class BCLIError(Exception):
    """Base exception for all bcli errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        bc_message: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.bc_message = bc_message
        self.correlation_id = correlation_id
        parts = [message]
        if bc_message and bc_message != message:
            parts.append(f"BC says: {bc_message}")
        if correlation_id:
            parts.append(f"Correlation ID: {correlation_id}")
        super().__init__(" | ".join(parts))


class AuthError(BCLIError):
    """Authentication failure (401, token acquisition)."""


class ForbiddenError(BCLIError):
    """Permission denied (403)."""


class NotFoundError(BCLIError):
    """Resource not found (404)."""


class ValidationError(BCLIError):
    """Bad request / OData filter error (400)."""


class ThrottledError(BCLIError):
    """Rate limited (429). Check retry_after attribute."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs) -> None:
        self.retry_after = retry_after
        super().__init__(message, **kwargs)


class ServerError(BCLIError):
    """Server error (500, 502, 503, 504)."""


class ConfigError(BCLIError):
    """Configuration missing or invalid."""


class RegistryError(BCLIError):
    """Endpoint not found in any registry."""


class SafetyError(BCLIError):
    """Write safety check failed (missing environment, company, or production confirmation)."""


class WorkflowError(BCLIError):
    """Workflow template resolution or execution error."""


class ExtractError(BCLIError):
    """PDF / document extraction failure (schema, backend, or PDF preflight)."""
