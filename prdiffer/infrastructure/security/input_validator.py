"""Input validation and sanitization for security.

Validates GitHub PR/GitLab MR URLs and free-text inputs against injection patterns
(SQL, command, path traversal) and sanitizes values for logging.
"""

from prdiffer.domain.entities.gitlab_merge_request_url import parse_gitlab_merge_request_url
from prdiffer.domain.exceptions import (
    InvalidURLError,
    InputSanitizationError,
    SuspiciousOperationError,
)
from prdiffer.infrastructure.security.injection_detector import InjectionDetector
from prdiffer.infrastructure.security.sanitizer import InputSanitizer


class InputValidator:
    """Validates and sanitizes user inputs for security (implements ``InputValidatorProtocol``)."""

    def __init__(self) -> None:
        self._detector = InjectionDetector()

    def validate_github_url(self, url: str) -> tuple[str, str, int]:
        """Validate and parse a GitHub PR URL.

        Args:
            url: GitHub PR URL to validate

        Returns:
            Tuple of (owner, repo, pr_number)

        Raises:
            InvalidURLError: If URL is invalid or malicious
        """
        from prdiffer.infrastructure.utils.url_parser import parse_github_pr_url

        url = url.strip()
        if not url:
            raise InvalidURLError("URL cannot be empty")

        if self._detector.check_suspicious_patterns(url):
            raise SuspiciousOperationError("URL contains suspicious patterns", details={"url": url[:100]})

        return parse_github_pr_url(url)

    def validate_gitlab_url(self, url: str) -> tuple[str, str, int]:
        """Validate and parse a canonical GitLab merge request URL."""
        url = url.strip()
        if not url:
            raise InvalidURLError("URL cannot be empty")

        if self._detector.check_suspicious_patterns(url):
            raise SuspiciousOperationError("URL contains suspicious patterns", details={"url": url[:100]})

        return parse_gitlab_merge_request_url(url)

    @classmethod
    def sanitize_string(cls, value: object, max_length: int = 1000) -> str:
        """Sanitize a string input.

        Args:
            value: String to sanitize
            max_length: Maximum allowed length

        Returns:
            Sanitized string

        Raises:
            InputSanitizationError: If input is not a string or is suspicious
            SuspiciousOperationError: If suspicious patterns detected
        """
        if not isinstance(value, str):
            raise InputSanitizationError(f"Expected string, got {type(value).__name__}")
        return InputSanitizer.sanitize_string(value, max_length)

    @classmethod
    def sanitize_for_logging(cls, value: object, max_length: int = 200) -> str:
        """Sanitize a value for safe logging.

        Args:
            value: Value to sanitize
            max_length: Maximum length for logged value

        Returns:
            Sanitized value safe for logging
        """
        return InputSanitizer.sanitize_for_logging(value, max_length)
