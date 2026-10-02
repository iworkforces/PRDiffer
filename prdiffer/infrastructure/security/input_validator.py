"""Input validation and sanitization for security.

This module provides comprehensive input validation to prevent:
- SQL injection
- Command injection
- Path traversal
- XSS attacks
- Malicious URLs
- Invalid data formats

Convenience functions and token/user/branch validation are in
input_validation_helpers.py.
"""

import re

from prdiffer.domain.exceptions import (
    InvalidURLError,
    InvalidRepositoryError,
    InvalidPRNumberError,
    InputSanitizationError,
    SuspiciousOperationError,
)
from prdiffer.infrastructure.security.injection_detector import (
    InjectionDetector,
    SecurityPatterns,
)
from prdiffer.infrastructure.security.sanitizer import (
    InputSanitizer,
)
from prdiffer.infrastructure.security.input_validation_helpers import (
    InputValidationHelpersMixin,
)


class InputValidator(InputValidationHelpersMixin):
    """Validates and sanitizes user inputs for security.

    This validator can be used in two ways:
    1. Instance with defaults: validator = InputValidator() - uses default patterns
    2. Instance with custom patterns: validator = InputValidator(security_patterns) - uses custom patterns

    Example with custom patterns from settings:
        from prdiffer.infrastructure.settings import get_settings_service
        from prdiffer.infrastructure.security.injection_detector import SecurityPatterns
        from prdiffer.infrastructure.security.input_validator import InputValidator

        settings = get_settings_service()
        patterns = SecurityPatterns.from_settings(settings)
        validator = InputValidator(security_patterns=patterns)
        owner, repo, pr = validator.validate_github_url(url)
    """

    GITHUB_URL_PATTERN: re.Pattern[str] = re.compile(r"^https://github\.com/([a-zA-Z0-9_-]+)/([a-zA-Z0-9._-]+)/pull/(\d+)/?$")
    GITHUB_REPO_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+$")
    SAFE_USERNAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]+$")
    SAFE_REPO_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9._-]+$")
    # Git branch/reference name validation
    # Based on Git ref naming rules:
    # - Can contain alphanumeric, hyphens, underscores, dots, and forward slashes
    # - Cannot start or end with slash
    # - Cannot have consecutive slashes
    # - Cannot start with dot
    # - Max length for Git refs is typically around 255 characters
    BRANCH_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9_\-./]*[a-zA-Z0-9])?$")

    def __init__(self, security_patterns: SecurityPatterns | None = None):
        """Initialize the InputValidator with optional custom security patterns.

        Args:
            security_patterns: Optional SecurityPatterns instance for custom pattern matching.
                If None, uses default class-level patterns.

        Example:
            >>> # Use default patterns
            >>> validator = InputValidator()

            >>> # Use custom patterns from settings
            >>> from prdiffer.infrastructure.settings import get_settings_service
            >>> settings = get_settings_service()
            >>> patterns = SecurityPatterns.from_settings(settings)
            >>> validator = InputValidator(security_patterns=patterns)
        """
        self._detector = InjectionDetector(security_patterns=security_patterns)
        self._sanitizer = InputSanitizer(detector=self._detector)

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
        from prdiffer.infrastructure.utils.url_parser import parse_gitlab_merge_request_url

        url = url.strip()
        if not url:
            raise InvalidURLError("URL cannot be empty")

        if self._detector.check_suspicious_patterns(url):
            raise SuspiciousOperationError("URL contains suspicious patterns", details={"url": url[:100]})

        return parse_gitlab_merge_request_url(url)

    @classmethod
    def validate_repository_identifier(cls, identifier: str) -> tuple[str, str]:
        """Validate a repository identifier (owner/repo format).

        Args:
            identifier: Repository identifier to validate

        Returns:
            Tuple of (owner, repo)

        Raises:
            InvalidRepositoryError: If identifier is invalid
        """
        if not identifier:
            raise InvalidRepositoryError("Repository identifier cannot be empty")

        if len(identifier) > 200:
            raise InvalidRepositoryError("Repository identifier too long")

        match = cls.GITHUB_REPO_PATTERN.match(identifier)
        if not match:
            raise InvalidRepositoryError(
                "Invalid repository format. Expected: owner/repo",
                details={"identifier": identifier},
            )

        parts = identifier.split("/")
        if len(parts) != 2:
            raise InvalidRepositoryError("Repository must be in format: owner/repo")

        owner, repo = parts
        cls._validate_github_owner(owner)
        cls._validate_repo_name(repo)

        return owner, repo

    @classmethod
    def _validate_github_owner(cls, owner: str):
        """Validate GitHub username/organization name.

        Args:
            owner: Username to validate

        Raises:
            InvalidRepositoryError: If owner is invalid
        """
        if not owner:
            raise InvalidRepositoryError("Owner cannot be empty")

        if len(owner) > 39:  # GitHub's max username length
            raise InvalidRepositoryError("Owner name too long (max 39 characters)")

        if not cls.SAFE_USERNAME_PATTERN.match(owner):
            raise InvalidRepositoryError(
                "Owner contains invalid characters (allowed: a-z, A-Z, 0-9, -, _)",
                details={"owner": owner},
            )

    @classmethod
    def _validate_repo_name(cls, repo: str):
        """Validate repository name.

        Args:
            repo: Repository name to validate

        Raises:
            InvalidRepositoryError: If repo name is invalid
        """
        if not repo:
            raise InvalidRepositoryError("Repository name cannot be empty")

        if len(repo) > 100:  # GitHub's max repo name length
            raise InvalidRepositoryError("Repository name too long (max 100 characters)")

        if not cls.SAFE_REPO_NAME_PATTERN.match(repo):
            raise InvalidRepositoryError("Repository name contains invalid characters", details={"repo": repo})

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
    def validate_pr_number(cls, pr_number: object) -> int:
        """Validate a PR number.

        Args:
            pr_number: PR number to validate

        Returns:
            Validated PR number

        Raises:
            InvalidPRNumberError: If PR number is invalid (not an integer, negative, or too large)
        """
        if not isinstance(pr_number, int):
            raise InvalidPRNumberError(f"PR number must be an integer, got {type(pr_number).__name__}")

        if pr_number <= 0:
            raise InvalidPRNumberError("PR number must be positive")

        if pr_number > 1000000:
            raise InvalidPRNumberError("PR number too large (max 1000000)")

        return pr_number

    @classmethod
    def validate_file_path(cls, file_path: object) -> str:
        """Validate a file path for cache keys, storage, and other safe operations.

        This validation prevents:
        - Path traversal attacks (../../etc/passwd)
        - Absolute paths being used where relative paths expected
        - Excessively long paths that could cause issues
        - Suspicious patterns that could indicate injection attempts

        Args:
            file_path: File path to validate

        Returns:
            Validated file path

        Raises:
            InputSanitizationError: If path is invalid
            SuspiciousOperationError: If path contains suspicious patterns

        Examples:
            >>> validate_file_path("cache/pr_123.json")
            'cache/pr_123.json'
            >>> validate_file_path("data/backup.tar.gz")
            'data/backup.tar.gz'
        """

        if not isinstance(file_path, str):
            raise InputSanitizationError(f"File path must be a string, got {type(file_path).__name__}")

        if not file_path:
            raise InputSanitizationError("File path cannot be empty")

        # Normalize the path to prevent bypass attempts with ./ or extra slashes
        file_path = file_path.replace("\\", "/")  # Normalize Windows paths
        while "//" in file_path:
            file_path = file_path.replace("//", "/")

        if len(file_path) > 500:
            raise InputSanitizationError("File path too long (max 500 characters)")

        from prdiffer.infrastructure.security.injection_detector import (
            InjectionDetector,
        )

        if InjectionDetector._PATH_TRAVERSAL_COMPILED.search(file_path):
            raise SuspiciousOperationError(
                "File path contains path traversal patterns",
                details={"path": file_path[:100]},
            )

        if file_path.startswith("/"):
            raise InputSanitizationError(
                "Absolute paths not allowed (use relative paths)",
                details={"path": file_path[:50]},
            )

        return file_path
