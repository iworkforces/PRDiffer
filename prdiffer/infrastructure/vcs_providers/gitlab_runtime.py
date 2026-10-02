"""Bounded GitLab SDK execution: capacity, deadlines, status-aware mapping.

Process-shared CapacityLimiter, operation-scoped python-gitlab clients,
per-call deadline + base_url, and typed exception translation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeGuard, runtime_checkable

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar
from urllib.parse import urlparse

import anyio
import gitlab
import requests

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.error_codes import (
    E1001_INVALID_URL,
    E2006_GITLAB_AUTH_FAILED,
    E2007_GITLAB_INSUFFICIENT_PERMISSIONS,
    E3006_GITLAB_RATE_LIMITED,
    E4001_REPO_NOT_FOUND,
    E4002_PR_NOT_FOUND,
    E4003_FILE_NOT_FOUND,
    E5004_TIMEOUT_ERROR,
    E5019_CONNECTION_ERROR,
    E5021_GITLAB_API_ERROR,
)
from prdiffer.domain.exceptions import (
    AuthenticationError,
    AuthorizationError,
    GitLabAPIError,
    InvalidURLError,
    RateLimitError,
    TimeoutError as DomainTimeoutError,
)

T = TypeVar("T")

GITLAB_COM_URL = "https://gitlab.com"


class GitLabNotFoundKind(StrEnum):
    """Context for mapping a verified 404 to the correct not-found code."""

    PROJECT = "project"
    MERGE_REQUEST = "merge_request"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class GitLabNotFoundContext:
    """Operation-supplied 404 interpretation (project / MR / file)."""

    kind: GitLabNotFoundKind


def _safe_status(exc: BaseException) -> int | None:
    code = getattr(exc, "response_code", None)
    return code if isinstance(code, int) else None


@runtime_checkable
class _SupportsInt(Protocol):
    def __int__(self) -> int: ...


@runtime_checkable
class _SupportsIndex(Protocol):
    def __index__(self) -> int: ...


def _is_response_headers(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _parse_retry_after(exc: BaseException, remaining: float) -> int | None:
    """Parse Retry-After from SDK exception headers when present; bound by remaining."""
    headers = getattr(exc, "response_headers", None)
    if not _is_response_headers(headers):
        return None
    raw = next((value for key, value in headers.items() if isinstance(key, str) and key.casefold() == "retry-after"), None)
    if raw is None:
        return None
    try:
        if not isinstance(raw, (str, bytes, bytearray, int, float, _SupportsInt, _SupportsIndex)):
            return None
        value = int(raw)
    except TypeError, ValueError:
        return None
    if value < 0:
        return None
    bound = max(0, int(math.floor(remaining)))
    return min(value, bound) if bound > 0 else 0


def cache_host_from_base_url(base_url: str) -> str:
    """Host identity for cache keys: hostname plus non-default port when present.

    Fail-closed when the URL has no hostname (never substitute gitlab.com).
    """
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    if not parsed.hostname:
        raise InvalidURLError(
            "GitLab base_url has no hostname",
            error_code=E1001_INVALID_URL,
            details={"base_url": base_url},
        )
    host = parsed.hostname.casefold()
    if parsed.port is not None and parsed.port not in (80, 443):
        return f"{host}:{parsed.port}"
    return host


def map_gitlab_exception(
    exc: BaseException,
    *,
    not_found: GitLabNotFoundContext | None = None,
    remaining_budget: float = 0.0,
) -> BaseException:
    """Translate python-gitlab / requests failures to domain exceptions.

    Never copies response_body, tokens, or credential-bearing URLs into details.
    """
    if isinstance(exc, (DomainTimeoutError, AuthenticationError, AuthorizationError, RateLimitError, GitLabAPIError, InvalidURLError)):
        return exc

    status = _safe_status(exc)

    if isinstance(exc, requests.Timeout) or (isinstance(exc, requests.RequestException) and "timeout" in type(exc).__name__.lower()):
        return DomainTimeoutError(
            "GitLab request timed out",
            error_code=E5004_TIMEOUT_ERROR,
            details={"status_code": status} if status is not None else None,
        )

    if isinstance(exc, (requests.ConnectionError, gitlab.GitlabConnectionError)):
        return GitLabAPIError(
            "Connection to GitLab failed",
            status_code=status,
            error_code=E5019_CONNECTION_ERROR,
            details={},
        )

    if status == 401 or isinstance(exc, gitlab.GitlabAuthenticationError):
        return AuthenticationError(
            "GitLab authentication failed",
            error_code=E2006_GITLAB_AUTH_FAILED,
            details={"status_code": 401},
        )

    if status == 403:
        return AuthorizationError(
            "Insufficient permissions for GitLab resource",
            error_code=E2007_GITLAB_INSUFFICIENT_PERMISSIONS,
            details={"status_code": 403},
        )

    if status == 404:
        kind = not_found.kind if not_found is not None else None
        if kind is GitLabNotFoundKind.PROJECT:
            return GitLabAPIError(
                "Repository not found",
                status_code=404,
                error_code=E4001_REPO_NOT_FOUND,
                details={"status_code": 404},
            )
        if kind is GitLabNotFoundKind.FILE:
            return GitLabAPIError(
                "File not found in repository",
                status_code=404,
                error_code=E4003_FILE_NOT_FOUND,
                details={"status_code": 404},
            )
        return GitLabAPIError(
            "Merge request not found",
            status_code=404,
            error_code=E4002_PR_NOT_FOUND,
            details={"status_code": 404},
        )

    if status == 429:
        retry_after = _parse_retry_after(exc, remaining_budget)
        return RateLimitError(
            "GitLab API rate limit exceeded",
            error_code=E3006_GITLAB_RATE_LIMITED,
            details={"status_code": 429},
            retry_after=retry_after,
        )

    if status is not None and status >= 500:
        return GitLabAPIError(
            "GitLab API error occurred",
            status_code=status,
            error_code=E5021_GITLAB_API_ERROR,
            details={"status_code": status},
        )

    if isinstance(exc, (gitlab.GitlabError, requests.RequestException)):
        return GitLabAPIError(
            "GitLab API error occurred",
            status_code=status,
            error_code=E5021_GITLAB_API_ERROR,
            details={"status_code": status} if status is not None else {},
        )

    return exc


class GitLabRuntime:
    """Shared limiter + operation-scoped client factory + async blocking runner.

    Request deadlines and base URLs are **per call**, never stored as shared
    mutable request state on this process-wide instance.
    """

    def __init__(
        self,
        config: GitLabConfig,
        private_token: str | None = None,
        *,
        base_url: str = GITLAB_COM_URL,
        limiter: anyio.CapacityLimiter | None = None,
        client_factory: Callable[..., gitlab.Gitlab] | None = None,
    ) -> None:
        self._config = config
        self._private_token = private_token
        self._default_base_url = (base_url or GITLAB_COM_URL).rstrip("/")
        self._limiter = limiter or anyio.CapacityLimiter(config.max_concurrent)
        self._client_factory = client_factory or gitlab.Gitlab
        self._closed_clients = 0

    @property
    def config(self) -> GitLabConfig:
        return self._config

    @property
    def limiter(self) -> anyio.CapacityLimiter:
        return self._limiter

    @property
    def closed_client_count(self) -> int:
        """Number of operation-scoped clients closed (for tests)."""
        return self._closed_clients

    def remaining_budget(self, deadline_monotonic: float | None) -> float:
        if deadline_monotonic is None:
            return float(self._config.pr_diff_request_timeout_seconds)
        return deadline_monotonic - time.monotonic()

    def ensure_host_allowed(self, base_url: str) -> None:
        """Reject SDK targets outside the configured host allowlist."""
        host = cache_host_from_base_url(base_url).split(":", 1)[0]
        if not self._config.is_host_allowed(host):
            raise InvalidURLError(
                f"GitLab host {host!r} is not in allowed_hosts {list(self._config.allowed_hosts)!r}",
                error_code=E1001_INVALID_URL,
                details={"host": host},
            )

    def create_client(
        self,
        *,
        remaining: float,
        base_url: str | None = None,
    ) -> gitlab.Gitlab:
        """Build a fresh python-gitlab client for one operation."""
        if remaining <= 0:
            raise DomainTimeoutError(
                "GitLab operation deadline exhausted",
                error_code=E5004_TIMEOUT_ERROR,
            )
        timeout = min(float(self._config.timeout), remaining)
        url = (base_url or self._default_base_url or GITLAB_COM_URL).rstrip("/")
        self.ensure_host_allowed(url)
        client = self._client_factory(
            url,
            private_token=self._private_token,
            timeout=timeout,
            retry_transient_errors=self._config.retry_transient_errors,
        )
        self._install_request_defaults(client)
        return client

    def _install_request_defaults(self, client: object) -> None:
        """Inject max_retries and obey_rate_limit defaults into SDK requests."""
        original = getattr(client, "http_request", None)
        if original is None or not callable(original):
            return
        max_retries = self._config.max_retries
        obey = self._config.obey_rate_limit

        def http_request(*args: object, **kwargs: object) -> object:
            kwargs.setdefault("max_retries", max_retries)
            kwargs.setdefault("obey_rate_limit", obey)
            return original(*args, **kwargs)

        setattr(client, "http_request", http_request)

    def close_client(self, client: object) -> None:
        """Close an operation-scoped client session when possible."""
        try:
            session = getattr(client, "session", None)
            if session is not None and hasattr(session, "close"):
                session.close()
        finally:
            self._closed_clients += 1

    async def run_blocking(
        self,
        callback: Callable[[gitlab.Gitlab], T],
        *,
        not_found: GitLabNotFoundContext | None = None,
        base_url: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> T:
        """Run a synchronous SDK callback with limiter, per-call deadline, fresh client.

        Deadline and base_url are per invocation (safe for process-shared runtime).
        Remaining budget is recomputed after limiter acquisition.
        """
        remaining_pre = self.remaining_budget(deadline_monotonic)
        if remaining_pre <= 0:
            raise DomainTimeoutError(
                "GitLab operation deadline exhausted",
                error_code=E5004_TIMEOUT_ERROR,
            )
        url = (base_url or self._default_base_url).rstrip("/")
        self.ensure_host_allowed(url)

        try:
            async with self._limiter:
                remaining = self.remaining_budget(deadline_monotonic)
                if remaining <= 0:
                    raise DomainTimeoutError(
                        "GitLab operation deadline exhausted while waiting for capacity",
                        error_code=E5004_TIMEOUT_ERROR,
                    )

                def worker() -> T:
                    # Recompute after thread start so queue wait is not double-counted
                    # into the SDK timeout alone; wall-clock still enforced below.
                    thread_remaining = self.remaining_budget(deadline_monotonic)
                    if thread_remaining <= 0:
                        raise DomainTimeoutError(
                            "GitLab operation deadline exhausted",
                            error_code=E5004_TIMEOUT_ERROR,
                        )
                    client = self.create_client(remaining=thread_remaining, base_url=url)
                    try:
                        return callback(client)
                    except BaseException as exc:
                        mapped = map_gitlab_exception(
                            exc,
                            not_found=not_found,
                            remaining_budget=thread_remaining,
                        )
                        if mapped is exc:
                            raise
                        raise mapped from None
                    finally:
                        self.close_client(client)

                run_sync = getattr(anyio.to_thread, "run_sync")
                # Hold capacity until worker finishes so abandon cannot oversubscribe
                # the shared limiter (no abandoned threads still holding work).
                result = await run_sync(worker, abandon_on_cancel=False)

                # abandon_on_cancel=False shields cancel scopes while the thread
                # runs; enforce the per-request deadline on completion. Real HTTP
                # is also bounded by create_client(timeout=min(config, remaining)).
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    raise DomainTimeoutError(
                        "GitLab operation timed out",
                        error_code=E5004_TIMEOUT_ERROR,
                    )
                return result
        except TimeoutError as exc:
            raise DomainTimeoutError(
                "GitLab operation timed out",
                error_code=E5004_TIMEOUT_ERROR,
            ) from exc
