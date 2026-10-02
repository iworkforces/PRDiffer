"""Typed file-content results for provider content acquisition.

Distinguishes valid empty text from deterministic unavailability. Operational
provider failures (auth, rate limit, transport, retry exhaustion) must raise
exceptions rather than becoming union values.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FileContentUnavailableReason(StrEnum):
    """Deterministic content unavailability reasons (not operational failures)."""

    BINARY_CONTENT = "BINARY_CONTENT"
    FILE_SIZE_LIMIT = "FILE_SIZE_LIMIT"
    DIRECTORY = "DIRECTORY"
    NOT_FOUND = "NOT_FOUND"
    CONTENT_DECODE_FAILED = "CONTENT_DECODE_FAILED"


@dataclass(frozen=True)
class FileContentAvailable:
    """Successfully acquired text content (including zero-byte files)."""

    text: str


@dataclass(frozen=True)
class FileContentUnavailable:
    """Deterministic inability to provide text for a selected path/ref."""

    reason: FileContentUnavailableReason
    path: str
    ref: str
    observed_size: int | None = None


FileContentResult = FileContentAvailable | FileContentUnavailable
