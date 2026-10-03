"""Error classification utilities for retry logic."""

from dataclasses import dataclass
from typing import Any, cast
from github import GithubException


PERMANENT_ERROR_CODES = {"404", "401", "403"}
SERVER_ERROR_CODES = {"500", "501", "502", "503", "504"}
TRANSIENT_ERROR_PATTERNS = {"timeout", "connection", "network", "503", "502", "504"}
SECONDARY_RATE_LIMIT_PATTERNS = {
    "secondary rate limit",
    "abuse detection",
    "abuse detection mechanism",
    "api abuse",
    "temporarily blocked",
}


def is_breaker_failure(error: Exception) -> bool:
    if isinstance(error, GithubException):
        if error.status in {401, 404, 422}:
            return False
        return (500 <= error.status < 600 or error.status == 429
                or is_secondary_rate_limit_error(error) or is_rate_limit_error(error))
    return isinstance(error, (TimeoutError, ConnectionError, OSError, EOFError))


def is_permanent_error(error_code: str) -> bool:
    return error_code in PERMANENT_ERROR_CODES


def is_server_error(error_code: str) -> bool:
    return error_code in SERVER_ERROR_CODES


def is_transient_error(error_message: str) -> bool:
    error_message_lower = error_message.lower()
    return any(pattern in error_message_lower for pattern in TRANSIENT_ERROR_PATTERNS)


def is_secondary_rate_limit_error(error: Exception) -> bool:
    message = get_error_message(error)
    return any(pattern in message for pattern in SECONDARY_RATE_LIMIT_PATTERNS)


def get_error_message(error: Exception) -> str:
    base_message = str(error)
    data_message = ""
    data = getattr(error, "data", None)
    if isinstance(data, dict):
        data_message = str(cast(dict[str, Any], data).get("message", ""))
    elif isinstance(data, str):
        data_message = data
    return f"{base_message} {data_message}".strip().lower()


def categorize_error(error: Exception) -> str:
    """Categorize error for health tracking.

    Returns: not_found, authentication, rate_limit, server_error, timeout, network, or unknown.
    """
    error_str = str(error).lower()

    if "404" in error_str:
        return "not_found"
    elif "403" in error_str or "401" in error_str:
        return "authentication"
    elif "429" in error_str or "rate limit" in error_str:
        return "rate_limit"
    elif any(f"{code}" in error_str for code in [500, 502, 503, 504]):
        return "server_error"
    elif "timeout" in error_str:
        return "timeout"
    elif "connection" in error_str or "network" in error_str:
        return "network"
    else:
        return "unknown"


def should_retry_by_error_code(
    error_message: str,
    retry_on_404: bool,
    retry_on_403: bool,
    retry_on_500: bool,
) -> bool:
    if "404" in error_message and not retry_on_404:
        return False
    if "403" in error_message and not retry_on_403:
        return False
    if any(code in error_message for code in SERVER_ERROR_CODES) and not retry_on_500:
        return False
    return True


def is_rate_limit_error(error: Exception) -> bool:
    error_str = str(error).lower()
    return is_secondary_rate_limit_error(error) or "rate limit" in error_str or "429" in str(error)


@dataclass
class RetryDecision:
    """Result of error classification for retry decision."""

    should_retry: bool
    reason: str
    is_rate_limit: bool
    is_permanent: bool


def classify_error_for_retry(
    error: Exception,
    retry_on_404: bool = False,
    retry_on_403: bool = True,
    retry_on_500: bool = True,
) -> RetryDecision:
    """Classify an error and return a RetryDecision."""
    error_message = str(error).lower()

    if "404" in error_message and not retry_on_404:
        return RetryDecision(
            should_retry=False,
            reason="404 Not Found (not configured for retry)",
            is_rate_limit=False,
            is_permanent=True,
        )

    if "403" in error_message and not retry_on_403:
        return RetryDecision(
            should_retry=False,
            reason="403 Forbidden (not configured for retry)",
            is_rate_limit=False,
            is_permanent=True,
        )

    if any(code in error_message for code in SERVER_ERROR_CODES) and not retry_on_500:
        return RetryDecision(
            should_retry=False,
            reason="Server error (not configured for retry)",
            is_rate_limit=False,
            is_permanent=True,
        )

    is_rate_limit = is_rate_limit_error(error)

    is_transient = is_transient_error(error_message)

    if is_transient or is_rate_limit:
        return RetryDecision(
            should_retry=True,
            reason="Transient error detected" if is_transient else "Rate limit detected",
            is_rate_limit=is_rate_limit,
            is_permanent=False,
        )

    return RetryDecision(
        should_retry=False,
        reason="Error not classified as retryable",
        is_rate_limit=False,
        is_permanent=True,
    )
