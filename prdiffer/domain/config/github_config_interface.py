"""Interface for GitHub configuration.

Defines the abstract interface for GitHub configuration,
enabling dependency inversion and testability.

Uses Protocol instead of ABC to work better with dataclasses.
"""

from typing import Protocol, TypedDict, Unpack, runtime_checkable


class GitHubConfigDict(TypedDict, total=False):
    """Typed dictionary for GitHub configuration."""

    rate_limit: int
    timeout: int
    max_retries: int
    retry_delay: float
    retry_on_404: bool
    retry_on_403: bool
    retry_on_500: bool
    retry_log_level: str
    permanent_failure_log_level: str
    circuit_breaker_enabled: bool
    circuit_breaker_failure_threshold: int
    circuit_breaker_timeout: int
    adaptive_retry_enabled: bool
    max_adaptive_delay: int
    api_health_tracking: bool
    context_aware_retry: bool
    ignore_patterns: list[str] | tuple[str, ...]
    valid_extensions: list[str] | tuple[str, ...]
    diff_parallel_threshold: int
    diff_max_workers: int
    max_files_allowed: int
    large_file_threshold: int
    chunk_size: int
    max_diff_size: int
    max_file_size_bytes: int
    parallel_file_fetch_enabled: bool
    parallel_diff_generation_enabled: bool
    pr_diff_request_timeout_seconds: float
    max_concurrent: int


@runtime_checkable
class GitHubConfigInterface(Protocol):
    """Protocol for GitHub configuration objects.

    Protocol is used instead of ABC to work better with dataclasses,
    as dataclass fields automatically provide the required attributes.
    """

    rate_limit: int
    timeout: int
    max_retries: int
    retry_delay: float
    retry_on_404: bool
    retry_on_403: bool
    retry_on_500: bool
    circuit_breaker_enabled: bool
    diff_parallel_threshold: int
    diff_max_workers: int
    ignore_patterns: tuple[str, ...]
    valid_extensions: tuple[str, ...]
    max_files_allowed: int
    max_file_size_bytes: int
    parallel_file_fetch_enabled: bool
    parallel_diff_generation_enabled: bool
    pr_diff_request_timeout_seconds: float
    max_concurrent: int

    def to_dict(self) -> "GitHubConfigDict":
        """Convert configuration to dictionary."""
        ...

    def with_overrides(self, **kwargs: Unpack["GitHubConfigDict"]) -> "GitHubConfigInterface":
        """Create new config with overridden values."""
        ...

    @property
    def should_use_circuit_breaker(self) -> bool:
        """Check if circuit breaker should be used."""
        ...

    def should_ignore_file(self, filename: str) -> bool:
        """Check if a file should be ignored based on patterns."""
        ...

    def has_valid_extension(self, filename: str) -> bool:
        """Check if a file has a valid extension."""
        ...

    def should_process_file(self, filename: str) -> bool:
        """Check if a file should be processed."""
        ...
