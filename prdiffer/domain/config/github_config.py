"""Centralized GitHub configuration dataclass.

Provides a frozen dataclass that centralizes all GitHub-related settings
in a single source of truth. Services receive a GitHubConfig object instead
of individual parameters.
"""

from dataclasses import dataclass, field
from typing import Any, Unpack, cast

from prdiffer.domain.exceptions import ConfigurationError

from .github_config_interface import GitHubConfigDict, GitHubConfigInterface

# Defaults match settings.toml / plan contracts.
DEFAULT_GITHUB_TIMEOUT_SECONDS = 30
DEFAULT_PR_DIFF_REQUEST_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_FILE_SIZE_BYTES = 10_485_760  # 10 MiB
DEFAULT_MAX_TOTAL_CHARS = 600_000


def _require_positive(name: str, value: object) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigurationError(f"{name} must be a positive number (got {value!r})")


@dataclass(frozen=True)
class GitHubConfig(GitHubConfigInterface):
    """Centralized configuration for GitHub API interactions.

    Immutable dataclass containing all GitHub-related settings.
    Uses tuple fields instead of lists for hashability (manual caching).
    """

    rate_limit: int = 5000
    timeout: int = DEFAULT_GITHUB_TIMEOUT_SECONDS
    max_retries: int = 3
    retry_delay: float = 1.0

    retry_on_404: bool = False
    retry_on_403: bool = True
    retry_on_500: bool = True
    retry_log_level: str = "DEBUG"
    permanent_failure_log_level: str = "INFO"

    circuit_breaker_enabled: bool = True
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_timeout: int = 60
    adaptive_retry_enabled: bool = True
    max_adaptive_delay: int = 30
    api_health_tracking: bool = True
    context_aware_retry: bool = True

    ignore_patterns: tuple[str, ...] = field(default_factory=tuple)
    valid_extensions: tuple[str, ...] = field(default_factory=tuple)

    diff_parallel_enabled: bool = True
    diff_parallel_threshold: int = 3
    diff_max_workers: int = 4
    diff_worker_timeout: float = 30.0

    max_files_allowed: int = 50

    large_file_threshold: int = 5000
    chunk_size: int = 1000
    max_diff_size: int = 100000

    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS
    # Bounded concurrency defaults (CapacityLimiter / max_concurrent when enabled).
    parallel_file_fetch_enabled: bool = True
    parallel_diff_generation_enabled: bool = True
    pr_diff_request_timeout_seconds: float = DEFAULT_PR_DIFF_REQUEST_TIMEOUT_SECONDS
    max_concurrent: int = 4

    def __post_init__(self) -> None:
        """Validate positive limits and timeout ordering."""
        for name, value in (
            ("rate_limit", self.rate_limit),
            ("timeout", self.timeout),
            ("max_retries", self.max_retries),
            ("retry_delay", self.retry_delay),
            ("circuit_breaker_failure_threshold", self.circuit_breaker_failure_threshold),
            ("circuit_breaker_timeout", self.circuit_breaker_timeout),
            ("max_adaptive_delay", self.max_adaptive_delay),
            ("diff_parallel_threshold", self.diff_parallel_threshold),
            ("diff_max_workers", self.diff_max_workers),
            ("diff_worker_timeout", self.diff_worker_timeout),
            ("max_files_allowed", self.max_files_allowed),
            ("large_file_threshold", self.large_file_threshold),
            ("chunk_size", self.chunk_size),
            ("max_diff_size", self.max_diff_size),
            ("max_file_size_bytes", self.max_file_size_bytes),
            ("max_total_chars", self.max_total_chars),
            ("pr_diff_request_timeout_seconds", self.pr_diff_request_timeout_seconds),
            ("max_concurrent", self.max_concurrent),
        ):
            _require_positive(name, value)

        if self.timeout >= self.pr_diff_request_timeout_seconds:
            raise ConfigurationError(
                "github.timeout must be strictly less than mcp.pr_diff_request_timeout_seconds "
                f"(got timeout={self.timeout}, request_timeout={self.pr_diff_request_timeout_seconds})"
            )

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> "GitHubConfig":
        """Create GitHubConfig from a dictionary."""
        raw_ignore_patterns = config.get("ignore_patterns")
        if raw_ignore_patterns is None:
            ignore_patterns: tuple[str, ...] = ()
        elif isinstance(raw_ignore_patterns, list):
            _patterns: list[str] = [str(p) for p in cast(list[object], raw_ignore_patterns)]
            ignore_patterns = tuple(_patterns)
        else:
            ignore_patterns = raw_ignore_patterns

        raw_valid_extensions = config.get("valid_extensions")
        if raw_valid_extensions is None:
            valid_extensions: tuple[str, ...] = ()
        elif isinstance(raw_valid_extensions, list):
            _extensions: list[str] = [str(e) for e in cast(list[object], raw_valid_extensions)]
            valid_extensions = tuple(_extensions)
        else:
            valid_extensions = raw_valid_extensions

        return cls(
            rate_limit=config.get("rate_limit", 5000),
            timeout=config.get("timeout", DEFAULT_GITHUB_TIMEOUT_SECONDS),
            max_retries=config.get("max_retries", 3),
            retry_delay=float(config.get("retry_delay", 1.0)),
            retry_on_404=config.get("retry_on_404", False),
            retry_on_403=config.get("retry_on_403", True),
            retry_on_500=config.get("retry_on_500", True),
            retry_log_level=config.get("retry_log_level", "DEBUG"),
            permanent_failure_log_level=config.get("permanent_failure_log_level", "INFO"),
            circuit_breaker_enabled=config.get("circuit_breaker_enabled", True),
            circuit_breaker_failure_threshold=config.get("circuit_breaker_failure_threshold", 5),
            circuit_breaker_timeout=config.get("circuit_breaker_timeout", 60),
            adaptive_retry_enabled=config.get("adaptive_retry_enabled", True),
            max_adaptive_delay=config.get("max_adaptive_delay", 30),
            api_health_tracking=config.get("api_health_tracking", True),
            context_aware_retry=config.get("context_aware_retry", True),
            ignore_patterns=ignore_patterns,
            valid_extensions=valid_extensions,
            diff_parallel_enabled=config.get("diff_parallel_enabled", True),
            diff_parallel_threshold=config.get("diff_parallel_threshold", 3),
            diff_max_workers=config.get("diff_max_workers", 4),
            diff_worker_timeout=float(config.get("diff_worker_timeout", 30.0)),
            max_files_allowed=config.get("max_files_allowed", 50),
            large_file_threshold=config.get("large_file_threshold", 5000),
            chunk_size=config.get("chunk_size", 1000),
            max_diff_size=config.get("max_diff_size", 100000),
            max_file_size_bytes=int(config.get("max_file_size_bytes", DEFAULT_MAX_FILE_SIZE_BYTES)),
            max_total_chars=int(config.get("max_total_chars", DEFAULT_MAX_TOTAL_CHARS)),
            parallel_file_fetch_enabled=bool(config.get("parallel_file_fetch_enabled", True)),
            parallel_diff_generation_enabled=bool(config.get("parallel_diff_generation_enabled", True)),
            pr_diff_request_timeout_seconds=float(config.get("pr_diff_request_timeout_seconds", DEFAULT_PR_DIFF_REQUEST_TIMEOUT_SECONDS)),
            max_concurrent=int(config.get("max_concurrent", 4)),
        )

    def to_dict(self) -> GitHubConfigDict:
        """Convert configuration to dictionary."""
        return {
            "rate_limit": self.rate_limit,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "retry_delay": self.retry_delay,
            "retry_on_404": self.retry_on_404,
            "retry_on_403": self.retry_on_403,
            "retry_on_500": self.retry_on_500,
            "retry_log_level": self.retry_log_level,
            "permanent_failure_log_level": self.permanent_failure_log_level,
            "circuit_breaker_enabled": self.circuit_breaker_enabled,
            "circuit_breaker_failure_threshold": self.circuit_breaker_failure_threshold,
            "circuit_breaker_timeout": self.circuit_breaker_timeout,
            "adaptive_retry_enabled": self.adaptive_retry_enabled,
            "max_adaptive_delay": self.max_adaptive_delay,
            "api_health_tracking": self.api_health_tracking,
            "context_aware_retry": self.context_aware_retry,
            "ignore_patterns": list(self.ignore_patterns),
            "valid_extensions": list(self.valid_extensions),
            "diff_parallel_enabled": self.diff_parallel_enabled,
            "diff_parallel_threshold": self.diff_parallel_threshold,
            "diff_max_workers": self.diff_max_workers,
            "diff_worker_timeout": self.diff_worker_timeout,
            "max_files_allowed": self.max_files_allowed,
            "large_file_threshold": self.large_file_threshold,
            "chunk_size": self.chunk_size,
            "max_diff_size": self.max_diff_size,
            "max_file_size_bytes": self.max_file_size_bytes,
            "max_total_chars": self.max_total_chars,
            "parallel_file_fetch_enabled": self.parallel_file_fetch_enabled,
            "parallel_diff_generation_enabled": self.parallel_diff_generation_enabled,
            "pr_diff_request_timeout_seconds": self.pr_diff_request_timeout_seconds,
            "max_concurrent": self.max_concurrent,
        }

    @property
    def github_worker_capacity(self) -> int:
        """Serialized GitHub worker capacity is one when parallel fetch is disabled."""
        if not self.parallel_file_fetch_enabled:
            return 1
        return self.max_concurrent

    def with_overrides(self, **kwargs: Unpack[GitHubConfigDict]) -> "GitHubConfig":
        """Create new config with overridden values."""
        current_dict: dict[str, Any] = {**self.to_dict()}
        for key, value in kwargs.items():
            if isinstance(value, list):
                current_dict[key] = tuple(cast(list[str], value))
            else:
                current_dict[key] = value
        return self.__class__.from_dict(current_dict)

    def should_ignore_file(self, filename: str) -> bool:
        """Check if a file should be ignored based on patterns."""
        import fnmatch

        filename_lower = filename.lower()
        for pattern in self.ignore_patterns:
            if fnmatch.fnmatch(filename_lower, pattern.lower()):
                return True
            if pattern.endswith("/") and pattern[:-1].lower() in filename_lower:
                return True
        return False

    def has_valid_extension(self, filename: str) -> bool:
        """Check if a file has a valid extension.

        Returns True if no extensions are configured (no restriction).
        """
        if not self.valid_extensions:
            return True

        for ext in self.valid_extensions:
            if filename.lower().endswith(ext.lower()):
                return True
        return False

    def should_process_file(self, filename: str) -> bool:
        """Check if a file should be processed (not ignored and has valid extension)."""
        return not self.should_ignore_file(filename) and self.has_valid_extension(filename)

    @property
    def should_use_circuit_breaker(self) -> bool:
        """Check if circuit breaker should be used."""
        return self.circuit_breaker_enabled

    @property
    def should_use_adaptive_retry(self) -> bool:
        """Check if adaptive retry should be used."""
        return self.adaptive_retry_enabled

    @property
    def should_track_api_health(self) -> bool:
        """Check if API health should be tracked."""
        return self.api_health_tracking

    @property
    def should_use_parallel_diff(self) -> bool:
        """Check if parallel diff processing should be used."""
        return self.diff_parallel_enabled
