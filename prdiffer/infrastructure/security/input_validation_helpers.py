"""Convenience validation functions and additional validation methods.

Extracted from input_validator.py for maintainability.
Contains convenience functions and token/user/branch validation.
"""

from __future__ import annotations

import re

from prdiffer.domain.exceptions import (
    InputSanitizationError,
    SuspiciousOperationError,
)
from prdiffer.infrastructure.security.injection_detector import (
    InjectionDetector,
    _detector,
)
from prdiffer.infrastructure.security.sanitizer import (
    sanitize_for_logging,
)


class InputValidationHelpersMixin:
    """Mixin providing token, user ID, and branch name validation.

    Requires the host class to provide:
        - BRANCH_NAME_PATTERN: re.Pattern[str]
    """

    # Type annotations for host class attributes used by this mixin
    BRANCH_NAME_PATTERN: re.Pattern[str]
    _detector: InjectionDetector

    @classmethod
    def validate_token(cls, token: object) -> str:
        """Validate an authentication token format.

        Args:
            token: Token to validate

        Returns:
            Validated token

        Raises:
            InputSanitizationError: If token format is invalid
        """

        if not isinstance(token, str):
            raise InputSanitizationError(f"Token must be a string, got {type(token).__name__}")

        if not token:
            raise InputSanitizationError("Token cannot be empty")

        if len(token) < 20:
            raise InputSanitizationError("Token too short (minimum 20 characters)")

        if len(token) > 500:
            raise InputSanitizationError("Token too long (maximum 500 characters)")

        if token != token.strip():
            raise InputSanitizationError("Token contains leading/trailing whitespace")

        if not re.match(r"^[a-zA-Z0-9_\-\.]+$", token):
            raise InputSanitizationError("Token contains invalid characters")

        return token

    @classmethod
    def validate_user_id(cls, user_id: object) -> str:
        """Validate a user ID.

        Args:
            user_id: User ID to validate

        Returns:
            Validated user ID

        Raises:
            InputSanitizationError: If user ID is invalid
        """

        if not isinstance(user_id, str):
            raise InputSanitizationError(f"User ID must be a string, got {type(user_id).__name__}")

        if not user_id:
            raise InputSanitizationError("User ID cannot be empty")

        if len(user_id) > 100:
            raise InputSanitizationError("User ID too long (max 100 characters)")

        if not re.match(r"^[a-zA-Z0-9_\-@\.]+$", user_id):
            raise InputSanitizationError("User ID contains invalid characters", details={"user_id": user_id[:50]})

        return user_id

    @classmethod
    def validate_branch_name(cls, branch: str) -> str:
        """Validate a Git branch or reference name.

        Args:
            branch: Branch or reference name to validate

        Returns:
            Validated branch name

        Raises:
            InputSanitizationError: If branch name is invalid
            SuspiciousOperationError: If branch contains suspicious patterns
        """

        if not branch:
            raise InputSanitizationError("Branch name cannot be empty")

        if len(branch) > 255:
            raise InputSanitizationError("Branch name too long (max 255 characters)")

        if _detector.check_suspicious_patterns(branch):
            raise SuspiciousOperationError(
                "Branch name contains suspicious patterns",
                details={"branch": branch[:100]},
            )

        if not cls.BRANCH_NAME_PATTERN.match(branch):
            raise InputSanitizationError(
                "Branch name contains invalid characters or format",
                details={
                    "branch": branch[:100],
                    "allowed": "alphanumeric, hyphens, underscores, dots, and forward slashes",
                },
            )

        if branch.startswith("/") or branch.endswith("/"):
            raise InputSanitizationError("Branch name cannot start or end with '/'")

        if "//" in branch:
            raise InputSanitizationError("Branch name cannot contain consecutive slashes")

        # Cannot start with dot (hidden file/path)
        if branch.startswith("."):
            raise InputSanitizationError("Branch name cannot start with '.'")

        # Cannot contain ".." (parent directory reference)
        if ".." in branch:
            raise SuspiciousOperationError("Branch name cannot contain '..'")

        return branch

    def _check_suspicious_patterns_instance(self, value: str) -> bool:
        """Instance method for checking suspicious patterns.

        Uses instance-level custom patterns if available, otherwise falls back
        to class-level default patterns for performance.

        Args:
            value: Value to check

        Returns:
            True if suspicious patterns found
        """
        return self._detector.check_suspicious_patterns(value)

    @classmethod
    def sanitize_for_logging(cls, value: str, max_length: int = 200) -> str:
        """Sanitize a value for safe logging.

        Args:
            value: Value to sanitize
            max_length: Maximum length for logged value

        Returns:
            Sanitized value safe for logging
        """
        return sanitize_for_logging(value, max_length)


# Module-level singleton validator (lazy import to avoid circular)
_validator = None


def _get_validator():
    """Get or create the module-level singleton InputValidator."""
    global _validator
    if _validator is None:
        from prdiffer.infrastructure.security.input_validator import InputValidator

        _validator = InputValidator()
    return _validator


def validate_github_url(url: str) -> tuple[str, str, int]:
    """Convenience function for URL validation."""
    return _get_validator().validate_github_url(url)


def validate_repository_identifier(identifier: str) -> tuple[str, str]:
    """Convenience function for repository identifier validation."""
    return _get_validator().validate_repository_identifier(identifier)


def sanitize_string(value: str, max_length: int = 1000) -> str:
    """Convenience function for string sanitization."""
    return _get_validator().sanitize_string(value, max_length)


def validate_token(token: str) -> str:
    """Convenience function for token validation."""
    return _get_validator().validate_token(token)


def validate_user_id(user_id: str) -> str:
    """Convenience function for user ID validation."""
    return _get_validator().validate_user_id(user_id)


def validate_branch_name(branch: str) -> str:
    """Convenience function for branch/ref name validation."""
    return _get_validator().validate_branch_name(branch)


def validate_pr_number(pr_number: int) -> int:
    """Convenience function for PR number validation.

    Args:
        pr_number: PR number to validate

    Returns:
        Validated PR number

    Raises:
        InvalidPRNumberError: If PR number is invalid

    Example:
        >>> validate_pr_number(123)
        123
        >>> validate_pr_number(0)  # Raises InvalidPRNumberError
    """
    return _get_validator().validate_pr_number(pr_number)


def validate_file_path(file_path: str) -> str:
    """Convenience function for file path validation.

    Useful for validating cache keys, file storage paths, and any user-provided
    file paths to prevent path traversal attacks.

    Args:
        file_path: File path to validate

    Returns:
        Validated file path

    Raises:
        InputSanitizationError: If path is invalid
        SuspiciousOperationError: If path contains suspicious patterns

    Example:
        >>> validate_file_path("cache/pr_123.json")
        'cache/pr_123.json'
        >>> validate_file_path("../etc/passwd")  # Raises SuspiciousOperationError
    """
    return _get_validator().validate_file_path(file_path)
