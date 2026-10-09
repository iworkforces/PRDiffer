"""Domain-specific exception hierarchy for PRDiffer.

This module defines custom exceptions for different error scenarios,
providing better error handling and more informative error messages.
"""

from enum import StrEnum
from typing import Any, Literal

from .error_codes import E1011_HEAD_SHA_MISMATCH, E5001_INTERNAL_ERROR, E5020_FULL_DIFF_INCOMPLETE, E5022_PROVIDER_CAPABILITY_UNAVAILABLE
from .errors import ErrorCode


class PRDifferException(Exception):
    """Base exception for all PRDiffer errors.

    All custom exceptions in the PRDiffer application should inherit from this
    base class to ensure consistent error handling and logging across the system.
    This exception provides a structured way to pass error context through the
    optional details dictionary and error code for programmatic handling.
    """

    def __init__(
        self,
        message: str,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ):
        """Initialize PRDifferException with message, error code, and optional details.

        Args:
            message (str): Human-readable error message describing what went wrong.
            error_code (Optional[ErrorCode]): Structured error code for programmatic handling.
                Defaults to E5001_INTERNAL_ERROR if not provided.
            details (Optional[dict[str, Any]]): Optional dictionary with additional
                error context for debugging and logging purposes. Defaults to None.
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code or E5001_INTERNAL_ERROR
        self.details: dict[str, Any] = details or {}

    def __str__(self) -> str:
        """Return formatted string with error code."""
        if self.error_code:
            return f"[{self.error_code.code}] {self.message}"
        return self.message


# ============================================================================
# Authentication & Authorization Exceptions
# ============================================================================


class AuthenticationError(PRDifferException):
    """Raised when authentication fails.

    This exception is raised when the system cannot authenticate a user or
    service, typically due to invalid credentials, missing authentication
    tokens, or authentication service failures.
    """

    pass


class InvalidTokenError(AuthenticationError):
    """Raised when provided token is invalid or malformed.

    This exception occurs when an authentication token fails validation,
    typically due to incorrect format, invalid characters, or structure
    that doesn't meet token specifications.
    """

    pass


class ExpiredTokenError(AuthenticationError):
    """Raised when authentication token has expired."""

    pass


class MissingTokenError(AuthenticationError):
    """Raised when required authentication token is not provided."""

    pass


class AuthorizationError(PRDifferException):
    """Raised when user lacks permission for requested operation."""

    pass


class InsufficientPermissionsError(AuthorizationError):
    """Raised when user has valid auth but lacks specific permissions."""

    pass


# ============================================================================
# Rate Limiting Exceptions
# ============================================================================


class RateLimitError(PRDifferException):
    """Raised when rate limit is exceeded."""

    def __init__(
        self,
        message: str,
        retry_after: int | None = None,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ):
        """Initialize with retry information.

        Args:
            message: Error message
            retry_after: Seconds until rate limit resets
            error_code: Structured error code
            details: Additional context
        """
        super().__init__(message, error_code, details)
        self.retry_after = retry_after


class GlobalRateLimitError(RateLimitError):
    """Raised when global server rate limit is exceeded."""

    pass


class UserRateLimitError(RateLimitError):
    """Raised when per-user rate limit is exceeded."""

    pass


# ============================================================================
# Validation Exceptions
# ============================================================================


class ValidationError(PRDifferException):
    """Raised when input validation fails."""

    pass


class InvalidURLError(ValidationError):
    """Raised when provided URL is invalid or malformed."""

    pass


class InvalidRepositoryError(ValidationError):
    """Raised when repository identifier is invalid."""

    pass


class InvalidPRNumberError(ValidationError):
    """Raised when PR number is invalid."""

    pass


class UnsupportedFormatError(ValidationError):
    """Raised when data format is not supported."""

    pass


class HeadSHAMismatchError(PRDifferException):
    """Raised when a head-bound approval targets a PR/MR whose head has moved.

    ``actual_head_sha`` is set only when the provider reported the current head.
    ``compliment_note`` is set only when a compliment note was already posted
    and the adapter tried to remove it: ``"deleted"`` or ``"cleanup_failed"``.
    """

    def __init__(
        self,
        *,
        expected_head_sha: str,
        actual_head_sha: str | None = None,
        compliment_note: Literal["deleted", "cleanup_failed"] | None = None,
    ) -> None:
        details: dict[str, Any] = {"expected_head_sha": expected_head_sha}
        if actual_head_sha is not None:
            details["actual_head_sha"] = actual_head_sha
        if compliment_note is not None:
            details["compliment_note"] = compliment_note
        super().__init__(
            "Pull request head no longer matches expected_head_sha; approval was not recorded",
            error_code=E1011_HEAD_SHA_MISMATCH,
            details=details,
        )
        self.expected_head_sha = expected_head_sha
        self.actual_head_sha = actual_head_sha
        self.compliment_note = compliment_note


# ============================================================================
# GitHub API Exceptions
# ============================================================================


class GitHubAPIError(PRDifferException):
    """Base exception for GitHub API errors."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ):
        """Initialize with HTTP status code.

        Args:
            message: Error message
            status_code: HTTP status code from GitHub API
            error_code: Structured error code
            details: Additional context
        """
        super().__init__(message, error_code, details)
        self.status_code = status_code


class ProviderCapabilityUnavailableError(PRDifferException):
    """Raised when a resolved provider does not advertise a requested MCP capability."""

    def __init__(self, operation: str) -> None:
        super().__init__(
            "The requested operation is not available for this provider",
            error_code=E5022_PROVIDER_CAPABILITY_UNAVAILABLE,
            details={"operation": operation},
        )


class GitLabAPIError(PRDifferException):
    """Base exception for GitLab API operational errors.

    Preserves an optional HTTP status code and safe structured details only.
    Never include tokens, credentials, response bodies, or raw file content.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, error_code, details)
        self.status_code = status_code


class RepositoryNotFoundError(GitHubAPIError):
    """Raised when GitHub repository is not found or not accessible."""

    pass


class PRNotFoundError(GitHubAPIError):
    """Raised when pull request is not found."""

    pass


class FileNotFoundError(GitHubAPIError):
    """Raised when file in repository is not found."""

    pass


class GitHubAuthenticationError(GitHubAPIError):
    """Raised when GitHub authentication fails."""

    pass


class GitHubConnectionError(GitHubAPIError):
    """Raised when connection to GitHub fails."""

    pass


class GitHubRateLimitError(GitHubAPIError):
    """Raised when GitHub API rate limit is exceeded."""

    def __init__(
        self,
        message: str,
        retry_after: int | None = None,
        status_code: int | None = None,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ):
        """Initialize with retry information.

        Args:
            message: Error message
            retry_after: Seconds until rate limit resets
            status_code: HTTP status code from GitHub API
            error_code: Structured error code
            details: Additional context
        """
        super().__init__(message, status_code, error_code, details)
        self.retry_after = retry_after


class FullDiffIncompleteReason(StrEnum):
    """Stable machine-readable reasons for E5020 full-diff incompleteness."""

    INVENTORY_TRUNCATED = "INVENTORY_TRUNCATED"
    FILE_COUNT_LIMIT = "FILE_COUNT_LIMIT"
    BINARY_CONTENT = "BINARY_CONTENT"
    FILE_SIZE_LIMIT = "FILE_SIZE_LIMIT"
    CONTENT_UNAVAILABLE = "CONTENT_UNAVAILABLE"
    CONTENT_DECODE_FAILED = "CONTENT_DECODE_FAILED"
    UNSUPPORTED_FILE_STATUS = "UNSUPPORTED_FILE_STATUS"
    DIFF_GENERATION_FAILED = "DIFF_GENERATION_FAILED"
    RESPONSE_SIZE_LIMIT = "RESPONSE_SIZE_LIMIT"
    SNAPSHOT_CHANGED = "SNAPSHOT_CHANGED"


_FULL_DIFF_SAFE_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "reason",
        "path",
        "previous_path",
        "observed",
        "limit",
    }
)
_FULL_DIFF_FORBIDDEN_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "token",
        "access_token",
        "api_key",
        "authorization",
        "password",
        "secret",
        "raw_content",
        "content",
        "body",
        "patch",
        "diff",
    }
)


def _sanitize_full_diff_details(
    reason: FullDiffIncompleteReason,
    details: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep only safe structured fields; never retain tokens or raw content."""
    safe: dict[str, Any] = {"reason": reason.value}
    if not details:
        return safe

    forbidden = sorted(key for key in details if key.casefold() in _FULL_DIFF_FORBIDDEN_DETAIL_KEYS)
    if forbidden:
        raise ValueError("FullDiffIncompleteError details must not include sensitive or raw content keys: " + ", ".join(forbidden))

    unknown = sorted(key for key in details if key not in _FULL_DIFF_SAFE_DETAIL_KEYS)
    if unknown:
        raise ValueError("FullDiffIncompleteError details contain unsupported keys: " + ", ".join(unknown))

    for key in ("path", "previous_path", "observed", "limit"):
        if key in details and details[key] is not None:
            safe[key] = details[key]
    # Caller may pass reason again; canonical enum value always wins.
    safe["reason"] = reason.value
    return safe


class FullDiffIncompleteError(GitHubAPIError):
    """Raised when a selected PR cannot be returned as a complete full-context diff.

    Maps to ``E5020_FULL_DIFF_INCOMPLETE``. Does not replace auth, permission,
    rate-limit, or retry-exhausted operational failures.
    """

    def __init__(
        self,
        reason: FullDiffIncompleteReason,
        message: str | None = None,
        *,
        path: str | None = None,
        previous_path: str | None = None,
        observed: int | str | None = None,
        limit: int | str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(details or {})
        if path is not None:
            merged["path"] = path
        if previous_path is not None:
            merged["previous_path"] = previous_path
        if observed is not None:
            merged["observed"] = observed
        if limit is not None:
            merged["limit"] = limit

        safe_details = _sanitize_full_diff_details(reason, merged)
        resolved_message = message or (f"Full diff incomplete: {reason.value}" + (f" for {path}" if path else ""))
        super().__init__(
            resolved_message,
            status_code=status_code,
            error_code=E5020_FULL_DIFF_INCOMPLETE,
            details=safe_details,
        )
        self.reason = reason


# ============================================================================
# Cache Exceptions
# ============================================================================


class CacheError(PRDifferException):
    """Base exception for cache-related errors."""

    pass


class CacheInvalidationError(CacheError):
    """Raised when cache invalidation fails."""

    pass


class CacheCorruptionError(CacheError):
    """Raised when cached data is corrupted."""

    pass


# ============================================================================
# Configuration Exceptions
# ============================================================================


class ConfigurationError(PRDifferException):
    """Raised when configuration is invalid or missing."""

    pass


class MissingConfigurationError(ConfigurationError):
    """Raised when required configuration is missing."""

    pass


class InvalidConfigurationError(ConfigurationError):
    """Raised when configuration value is invalid."""

    pass


class SecretsError(ConfigurationError):
    """Raised when secrets management fails."""

    pass


# ============================================================================
# Processing Exceptions
# ============================================================================


class ProcessingError(PRDifferException):
    """Base exception for data processing errors."""

    pass


class DiffGenerationError(ProcessingError):
    """Raised when diff generation fails."""

    pass


class FileProcessingError(ProcessingError):
    """Raised when file processing fails."""

    pass


class PatternMatchingError(ProcessingError):
    """Raised when pattern matching fails."""

    pass


# ============================================================================
# Resource Exceptions
# ============================================================================


class ResourceError(PRDifferException):
    """Base exception for resource-related errors."""

    pass


class ResourceExhaustedError(ResourceError):
    """Raised when system resources are exhausted."""

    pass


class MemoryLimitError(ResourceError):
    """Raised when memory limit is exceeded."""

    pass


class TimeoutError(ResourceError):
    """Raised when operation times out."""

    pass


# ============================================================================
# Security Exceptions
# ============================================================================


class SecurityError(PRDifferException):
    """Base exception for security-related errors."""

    pass


class SuspiciousOperationError(SecurityError):
    """Raised when suspicious activity is detected."""

    pass


class InputSanitizationError(SecurityError):
    """Raised when input contains potentially malicious content."""

    pass


class SignatureVerificationError(SecurityError):
    """Raised when request signature verification fails."""

    pass


# ============================================================================
# Helper Functions
# ============================================================================


def get_exception_details(exception: Exception) -> dict[str, Any]:
    """Extract details from an exception for logging.

    Args:
        exception: Exception to extract details from

    Returns:
        Dictionary with exception details
    """
    details: dict[str, Any] = {
        "type": type(exception).__name__,
        "message": str(exception),
    }

    if isinstance(exception, PRDifferException):
        details["details"] = exception.details

    if isinstance(exception, (GitHubAPIError, GitLabAPIError)):
        details["status_code"] = exception.status_code

    if isinstance(exception, RateLimitError):
        details["retry_after"] = exception.retry_after

    return details


def wrap_github_exception(exception: Exception) -> GitHubAPIError:
    """Wrap generic exceptions from GitHub library into typed exceptions.

    Args:
        exception: Exception from PyGithub or requests

    Returns:
        Appropriate GitHubAPIError subclass
    """
    error_msg = str(exception).lower()

    if "404" in error_msg or "not found" in error_msg:
        if "repository" in error_msg:
            return RepositoryNotFoundError(str(exception))
        elif "pull" in error_msg or "pr" in error_msg:
            return PRNotFoundError(str(exception))
        else:
            return FileNotFoundError(str(exception))

    if "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg:
        return GitHubAuthenticationError(str(exception))

    if "429" in error_msg or "rate limit" in error_msg:
        return GitHubRateLimitError(
            str(exception),
            retry_after=60,  # Default retry after 60 seconds
        )

    if "timeout" in error_msg or "connection" in error_msg:
        return GitHubConnectionError(str(exception))

    # Default to generic GitHub API error
    return GitHubAPIError(str(exception))
