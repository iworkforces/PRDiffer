"""Input sanitization for security.

This module provides sanitization methods to clean user input and prevent
security vulnerabilities in logs and output.
"""

from prdiffer.domain.exceptions import InputSanitizationError, SuspiciousOperationError
from prdiffer.infrastructure.security.injection_detector import _detector


class InputSanitizer:
    """Sanitizes input strings for security.

    This class provides methods to sanitize strings for safe use in logging,
    storage, and other contexts where security is critical.

    Usage:
        >>> InputSanitizer.sanitize_string("Hello World")
        'Hello World'
        >>> InputSanitizer.sanitize_for_logging("Long text...", max_length=100)
        'Long text...'
    """

    @classmethod
    def sanitize_string(cls, value: str, max_length: int = 1000) -> str:
        """Sanitize a string input.

        Args:
            value: String to sanitize
            max_length: Maximum allowed length

        Returns:
            Sanitized string

        Raises:
            InputSanitizationError: If input is suspicious
            SuspiciousOperationError: If suspicious patterns detected
        """
        if len(value) > max_length:
            raise InputSanitizationError(f"String too long (max {max_length} characters)")

        if "\x00" in value:
            raise InputSanitizationError("String contains null bytes")

        if _detector.check_suspicious_patterns(value):
            raise SuspiciousOperationError("String contains suspicious patterns")

        sanitized = "".join(char for char in value if char in "\t\n\r" or not (0 <= ord(char) < 32))

        return sanitized

    @classmethod
    def sanitize_for_logging(cls, value: object, max_length: int = 200) -> str:
        """Sanitize a value for safe logging.

        Args:
            value: Value to sanitize
            max_length: Maximum length for logged value

        Returns:
            Sanitized value safe for logging
        """
        if not isinstance(value, str):
            value = str(value)

        if len(value) > max_length:
            value = value[:max_length] + "..."

        sanitized = "".join(char if char.isprintable() or char in "\t\n\r" else "?" for char in value)

        return sanitized
