"""Centralized GitLab strict full-diff configuration.

Frozen, slotted value object for GitLab SDK timeouts, capacity, content
limits, and host allowlist. Independent of GitHubConfig (no extension filtering).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypeGuard

# Defaults match settings.toml / plan contracts.
DEFAULT_GITLAB_TIMEOUT_SECONDS = 30
DEFAULT_PR_DIFF_REQUEST_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_FILE_SIZE_BYTES = 10_485_760  # 10 MiB
DEFAULT_MAX_FILES_ALLOWED = 50
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_CONCURRENT = 4
DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = ("gitlab.com",)


def _require_positive(name: str, value: object) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive number (got {value!r})")


def _require_non_negative_int(name: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer (got {value!r})")


def _as_int(raw: Any, default: int) -> int:
    if raw is None:
        return default
    return int(raw)


def _as_float(raw: Any, default: float) -> float:
    if raw is None:
        return default
    return float(raw)


def _as_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    return bool(raw)


def _is_host_sequence(value: object) -> TypeGuard[list[object] | tuple[object, ...]]:
    return isinstance(value, (list, tuple))


def _normalized_hosts(values: Iterable[object], default: tuple[str, ...]) -> tuple[str, ...]:
    parts = [str(value).strip().casefold() for value in values if str(value).strip()]
    return tuple(parts) if parts else default


def _as_host_tuple(raw: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return default
    if isinstance(raw, str):
        return _normalized_hosts(raw.split(","), default)
    if _is_host_sequence(raw):
        return _normalized_hosts(raw, default)
    raise ValueError(f"allowed_hosts must be a list/tuple/str (got {type(raw)!r})")


@dataclass(frozen=True, slots=True)
class GitLabConfig:
    """Immutable GitLab strict-diff limits, resilience, and host policy."""

    timeout: int = DEFAULT_GITLAB_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    retry_transient_errors: bool = True
    obey_rate_limit: bool = True
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES
    max_files_allowed: int = DEFAULT_MAX_FILES_ALLOWED
    pr_diff_request_timeout_seconds: float = DEFAULT_PR_DIFF_REQUEST_TIMEOUT_SECONDS
    # Hostnames only (casefolded). Default GitLab.com-only; opt-in custom hosts via settings.
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS

    def __post_init__(self) -> None:
        _require_positive("timeout", self.timeout)
        _require_non_negative_int("max_retries", self.max_retries)
        _require_positive("max_concurrent", self.max_concurrent)
        _require_positive("max_file_size_bytes", self.max_file_size_bytes)
        _require_positive("max_files_allowed", self.max_files_allowed)
        _require_positive("pr_diff_request_timeout_seconds", self.pr_diff_request_timeout_seconds)

        if self.timeout >= self.pr_diff_request_timeout_seconds:
            raise ValueError(
                "gitlab.timeout must be strictly less than mcp.pr_diff_request_timeout_seconds "
                f"(got timeout={self.timeout}, request_timeout={self.pr_diff_request_timeout_seconds})"
            )
        if not self.allowed_hosts:
            raise ValueError("allowed_hosts must contain at least one hostname")
        for host in self.allowed_hosts:
            if not host or "/" in host or ":" in host or " " in host:
                raise ValueError(f"allowed_hosts entries must be bare hostnames (got {host!r})")

    def is_host_allowed(self, host: str) -> bool:
        """True when hostname (or hostname:port netloc host part) is allowlisted."""
        bare = host.casefold().split(":", 1)[0]
        return bare in self.allowed_hosts

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> GitLabConfig:
        """Create GitLabConfig from a dictionary of overrides."""
        return cls(
            timeout=_as_int(config.get("timeout"), DEFAULT_GITLAB_TIMEOUT_SECONDS),
            max_retries=_as_int(config.get("max_retries"), DEFAULT_MAX_RETRIES),
            max_concurrent=_as_int(config.get("max_concurrent"), DEFAULT_MAX_CONCURRENT),
            retry_transient_errors=_as_bool(config.get("retry_transient_errors"), True),
            obey_rate_limit=_as_bool(config.get("obey_rate_limit"), True),
            max_file_size_bytes=_as_int(config.get("max_file_size_bytes"), DEFAULT_MAX_FILE_SIZE_BYTES),
            max_files_allowed=_as_int(config.get("max_files_allowed"), DEFAULT_MAX_FILES_ALLOWED),
            pr_diff_request_timeout_seconds=_as_float(
                config.get("pr_diff_request_timeout_seconds"),
                DEFAULT_PR_DIFF_REQUEST_TIMEOUT_SECONDS,
            ),
            allowed_hosts=_as_host_tuple(config.get("allowed_hosts"), DEFAULT_ALLOWED_HOSTS),
        )
