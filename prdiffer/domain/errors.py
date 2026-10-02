"""Structured error codes and exceptions for PRDiffer.

This module defines standardized error codes and custom exceptions
for the MCP server. Error codes follow the format E{category}{number}_{NAME}.

Error code constants live in error_codes.py, which imports ErrorCode and
ErrorCategory from this module; import constants from error_codes directly.

Error Categories:
- E1xxx: Input validation errors
- E2xxx: Authentication/authorization errors
- E3xxx: Rate limiting errors
- E4xxx: Resource not found errors
- E5xxx: Internal server errors

Each error includes:
- Error code: Unique identifier for programmatic handling
- Message: Human-readable error description
- Remediation: Suggested fix for the error
"""

from dataclasses import dataclass
from typing import Any
from enum import Enum


class ErrorCategory(str, Enum):
    """Error category enumeration."""

    INPUT_VALIDATION = "1"
    AUTHENTICATION = "2"
    RATE_LIMITING = "3"
    NOT_FOUND = "4"
    INTERNAL = "5"


@dataclass(frozen=True)
class ErrorCode:
    """Structured error code definition."""

    code: str
    name: str
    message: str
    remediation: str
    category: ErrorCategory

    def __str__(self) -> str:
        return f"{self.code}_{self.name}"

    def to_dict(self) -> dict[str, Any]:
        """Convert error code to dictionary for API response."""
        return {
            "error_code": str(self),
            "message": self.message,
            "remediation": self.remediation,
            "category": self.category.name,
        }


# =============================================================================
# Custom Exceptions
# =============================================================================


class MCPError(Exception):
    """Base exception for MCP server errors with structured error code."""

    def __init__(
        self,
        error_code: ErrorCode,
        detail: str | None = None,
        context: dict[str, Any] | None = None,
    ):
        self.error_code = error_code
        self.detail = detail
        self.context = context or {}
        super().__init__(str(error_code))

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for API response."""
        result = self.error_code.to_dict()
        if self.detail:
            result["detail"] = self.detail
        if self.context:
            result["context"] = self.context
        return result


class InputValidationError(MCPError):
    """Input validation error."""

    pass


class AuthenticationError(MCPError):
    """Authentication/authorization error."""

    pass


class RateLimitError(MCPError):
    """Rate limiting error."""

    pass


class ResourceNotFoundError(MCPError):
    """Resource not found error."""

    pass


class InternalServerError(MCPError):
    """Internal server error."""

    pass


# =============================================================================
# Error Code Mapping Utilities
# =============================================================================


def get_error_for_exception(exception: Exception) -> ErrorCode:
    """Map a Python exception to the appropriate error code.

    Args:
        exception: The exception to map

    Returns:
        ErrorCode: The most appropriate error code
    """
    import prdiffer.domain.error_codes as _ec

    exception_type = type(exception).__name__

    # Map known exception types to error codes
    error_mapping = {
        # GitHub exceptions
        "GithubException": _ec.E5002_GITHUB_API_ERROR,
        "RateLimitExceededException": _ec.E3001_RATE_LIMITED,
        "UnknownObjectException": _ec.E4001_REPO_NOT_FOUND,
        "BadCredentialsException": _ec.E2002_AUTH_FAILED,
        # Security exceptions
        "InvalidURLError": _ec.E1001_INVALID_URL,
        "InvalidRepositoryError": _ec.E1002_INVALID_REPOSITORY,
        "InvalidPRNumberError": _ec.E1003_INVALID_PR_NUMBER,
        "SuspiciousOperationError": _ec.E1004_SUSPICIOUS_INPUT,
        "InputSanitizationError": _ec.E1004_SUSPICIOUS_INPUT,
        # Standard exceptions
        "TimeoutError": _ec.E5004_TIMEOUT_ERROR,
        "ConnectionError": _ec.E5002_GITHUB_API_ERROR,
        "ValueError": _ec.E1001_INVALID_URL,
    }

    return error_mapping.get(exception_type, _ec.E5001_INTERNAL_ERROR)


def create_error_response(
    error_code: ErrorCode,
    detail: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a standardized error response.

    Args:
        error_code: The error code to include
        detail: Optional additional detail
        context: Optional context information

    Returns:
        Dict[str, Any]: Standardized error response
    """
    error_dict: dict[str, Any] = error_code.to_dict()

    if detail:
        error_dict["detail"] = detail

    if context:
        error_dict["context"] = context

    response: dict[str, Any] = {
        "success": False,
        "error": error_dict,
    }

    return response


__all__ = [
    "AuthenticationError",
    "ErrorCategory",
    "ErrorCode",
    "InternalServerError",
    "InputValidationError",
    "MCPError",
    "RateLimitError",
    "ResourceNotFoundError",
    "create_error_response",
    "get_error_for_exception",
]
