"""Exception sanitization utilities for secure logging."""

import re
import traceback
from typing import Any


class ExceptionSanitizer:
    GITHUB_TOKEN_PATTERNS = [
        r"(ghp_[a-zA-Z0-9]{36})",
        r"(gho_[a-zA-Z0-9]{36})",
        r"(ghu_[a-zA-Z0-9]{36})",
        r"(ghs_[a-zA-Z0-9]{36})",
        r"(ghr_[a-zA-Z0-9]{36})",
        r"(github_pat_[a-zA-Z0-9_]{82})",
    ]

    GENERIC_TOKEN_PATTERNS = [
        r'(["\']?token["\']?\s*[:=]\s*["\'])([a-zA-Z0-9_\-]{20,})(["\'])',
        r'(["\']?api_key["\']?\s*[:=]\s*["\'])([a-zA-Z0-9_\-]{20,})(["\'])',
        r'(["\']?authorization["\']?\s*[:=]\s*["\'])([a-zA-Z0-9_\-]{20,})(["\'])',
        r"(Bearer\s+)([a-zA-Z0-9_\-\.]{20,})",
    ]

    PASSWORD_PATTERNS = [
        r'(["\']?password["\']?\s*[:=]\s*["\'])([^"\']{8,})(["\'])',
        r'(["\']?passwd["\']?\s*[:=]\s*["\'])([^"\']{8,})(["\'])',
        r'(["\']?pwd["\']?\s*[:=]\s*["\'])([^"\']{8,})(["\'])',
    ]

    EMAIL_PATTERN = r"([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})"

    IP_PATTERN = r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})"

    API_KEY_IN_URL = r"([&?](api_key|token|access_token|secret|password)[=][^&\s]{8,})"

    @classmethod
    def sanitize_exception_message(cls, exception: Exception, max_length: int = 500) -> str:
        """Sanitize an exception message for safe logging.

        Args:
            exception: The exception to sanitize
            max_length: Maximum length of the sanitized message

        Returns:
            Sanitized exception message safe for logging
        """
        if not exception:
            return ""

        message = str(exception)

        sanitized = cls._sanitize_string(message)

        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "..."

        return sanitized

    @classmethod
    def sanitize_traceback(
        cls,
        exc_value: BaseException | None = None,
        max_frames: int = 10,
    ) -> str:
        """Sanitize a traceback for safe logging.

        Args:
            exc_value: Exception instance
            max_frames: Maximum number of stack frames to include

        Returns:
            Sanitized traceback string safe for logging
        """
        if exc_value is None:
            return ""

        tb_lines = traceback.format_exception(exc_value)

        if len(tb_lines) > max_frames * 2 + 2:  # Approximate lines per frame
            header = tb_lines[:2]
            frames = tb_lines[2 : max_frames * 2 + 2]
            tb_lines = header + frames + ["... (truncated)\n"]

        sanitized_lines = [cls._sanitize_string(line) for line in tb_lines]

        return "".join(sanitized_lines)

    @classmethod
    def sanitize_exception_for_logging(
        cls,
        exception: Exception,
        include_traceback: bool = False,
        max_length: int = 1000,
    ) -> dict[str, Any]:
        """Create a safe logging representation of an exception.

        Args:
            exception: The exception to sanitize
            include_traceback: Whether to include sanitized traceback
            max_length: Maximum length for each field

        Returns:
            Dictionary with sanitized exception information
        """
        exc_type = type(exception)
        exc_value = exception
        exc_traceback = exception.__traceback__

        result = {
            "type": exc_type.__name__,
            "module": exc_type.__module__,
            "message": cls.sanitize_exception_message(exc_value, max_length),
        }

        if include_traceback and exc_traceback is not None:
            result["traceback"] = cls.sanitize_traceback(exc_value)[:max_length]

        return result

    @classmethod
    def _sanitize_string(cls, value: object) -> str:
        """Apply all sanitization patterns to a string.

        Args:
            value: String to sanitize

        Returns:
            Sanitized string
        """

        if not isinstance(value, str):
            value = str(value)

        sanitized = value

        for pattern in cls.GITHUB_TOKEN_PATTERNS:
            sanitized = re.sub(
                pattern,
                lambda m: m.group(1)[:8] + "*" * (len(m.group(1)) - 8) if m.group(1) else m.group(0),
                sanitized,
                flags=re.IGNORECASE,
            )

        for pattern in cls.GENERIC_TOKEN_PATTERNS:
            sanitized = re.sub(
                pattern,
                lambda m: m.group(1) + m.group(2)[:4] + "*" * (len(m.group(2)) - 4) + m.group(3),
                sanitized,
                flags=re.IGNORECASE,
            )

        for pattern in cls.PASSWORD_PATTERNS:
            sanitized = re.sub(
                pattern,
                lambda m: m.group(1) + "*" * 8 + m.group(3),
                sanitized,
                flags=re.IGNORECASE,
            )

        # Partially redact emails (username is ok, domain should be preserved)
        sanitized = re.sub(
            cls.EMAIL_PATTERN,
            lambda m: m.group(1)[:3] + "***@" + m.group(2),
            sanitized,
        )

        sanitized = re.sub(
            cls.IP_PATTERN,
            lambda m: m.group(1) + ".*." + m.group(4),
            sanitized,
        )

        sanitized = re.sub(
            cls.API_KEY_IN_URL,
            lambda m: m.group(1)[:8] + "***",
            sanitized,
        )

        return sanitized

    @classmethod
    def redact_auth_header(cls, header_value: str) -> str:
        """Redact an Authorization header value.

        Args:
            header_value: Authorization header value

        Returns:
            Redacted header value
        """
        if not header_value:
            return ""

        header_lower = header_value.lower()

        if header_lower.startswith("bearer "):
            token = header_value[7:].strip()
            if len(token) > 10:
                return f"Bearer {token[:4]}...{token[-4:]}"
            return "Bearer ****"

        if header_lower.startswith("basic "):
            return "Basic ****"

        for prefix in ["token ", "apikey ", "api-key "]:
            if header_lower.startswith(prefix):
                return f"{prefix.title()}****"

        if len(header_value) > 10:
            return f"{header_value[:4]}...{header_value[-4:]}"

        return "****"


_sanitizer = ExceptionSanitizer()


def sanitize_exception_message(exception: Exception, max_length: int = 500) -> str:
    """Convenience function for sanitizing exception messages.

    Args:
        exception: The exception to sanitize
        max_length: Maximum length of the sanitized message

    Returns:
        Sanitized exception message safe for logging
    """
    return _sanitizer.sanitize_exception_message(exception, max_length)


def sanitize_traceback(
    exc_value: BaseException | None = None,
    max_frames: int = 10,
) -> str:
    """Convenience function for sanitizing tracebacks.

    Args:
        exc_value: Exception instance
        max_frames: Maximum number of stack frames to include

    Returns:
        Sanitized traceback string safe for logging
    """
    return _sanitizer.sanitize_traceback(exc_value=exc_value, max_frames=max_frames)


def sanitize_exception_for_logging(
    exception: Exception,
    include_traceback: bool = False,
    max_length: int = 1000,
) -> dict[str, Any]:
    """Convenience function for creating safe exception logging representations.

    Args:
        exception: The exception to sanitize
        include_traceback: Whether to include sanitized traceback
        max_length: Maximum length for each field

    Returns:
        Dictionary with sanitized exception information
    """
    return _sanitizer.sanitize_exception_for_logging(exception, include_traceback, max_length)


def redact_auth_header(header_value: str) -> str:
    """Convenience function for redacting authorization headers.

    Args:
        header_value: Authorization header value

    Returns:
        Redacted header value
    """
    return _sanitizer.redact_auth_header(header_value)
