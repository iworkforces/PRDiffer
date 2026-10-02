"""Protocol definition for input validation and sanitization.

This module defines the InputValidatorProtocol that infrastructure
implementations must satisfy, following Clean Architecture principles.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class InputValidatorProtocol(Protocol):
    """Protocol for input validation and sanitization.

    Defines the contract for validating and sanitizing user inputs
    to prevent injection attacks, path traversal, and other security threats.
    """

    def validate_github_url(self, url: str) -> tuple[str, str, int]:
        """Validate and parse a GitHub PR URL.

        Args:
            url: GitHub PR URL to validate

        Returns:
            Tuple of (owner, repo, pr_number)
        """
        ...

    def validate_gitlab_url(self, url: str) -> tuple[str, str, int]:
        """Validate and parse a canonical GitLab merge request URL."""
        ...

    def sanitize_string(self, value: str, max_length: int = 1000) -> str:
        """Sanitize a string input.

        Args:
            value: String to sanitize
            max_length: Maximum allowed length

        Returns:
            Sanitized string
        """
        ...

    def sanitize_for_logging(self, value: str, max_length: int = 200) -> str:
        """Sanitize a value for safe logging.

        Args:
            value: Value to sanitize
            max_length: Maximum length for logged value

        Returns:
            Sanitized value safe for logging
        """
        ...
