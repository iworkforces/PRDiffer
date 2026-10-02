"""Structured error code types for PRDiffer.

Error codes follow the format E{category}{number}_{NAME}. Constants live in
error_codes.py, which imports ErrorCode and ErrorCategory from this module;
import constants from error_codes directly.

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
