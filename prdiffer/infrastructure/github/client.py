"""GitHub API client with retry, circuit breaker, and ETag support.

File content operations and caching are in client_operations.py.
"""

from collections import OrderedDict
from typing import Any, cast

from github import Github
from github.Auth import Token
from github.Repository import Repository as PyGithubRepository
from github.PullRequest import PullRequest as PyGithubPullRequest

from prdiffer.domain.services.github_api import GitHubAPIServiceInterface
from prdiffer.domain.entities.repository import Repository
from prdiffer.domain.entities.pull_request import PullRequest
from prdiffer.infrastructure.github.mappers import (
    map_pygithub_repository_to_domain,
    map_pygithub_pr_to_domain,
)
from prdiffer.infrastructure.utils.retry.factories import (
    get_retry_handler,
    get_advanced_retry_handler,
)
from prdiffer.infrastructure.utils.retry.models import OperationContext
from prdiffer.infrastructure.logging.console_logger import ConsoleLogger, get_logger
from prdiffer.infrastructure.logging.exception_utils import (
    sanitize_exception_for_logging,
)
from prdiffer.infrastructure.settings import get_settings_service
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.domain.error_codes import E5009_CONFIGURATION_ERROR
from prdiffer.infrastructure.utils.parallel.executor import (
    AsyncParallelExecutor,
)
from prdiffer.infrastructure.utils.parallel.results import ErrorStrategy
from prdiffer.infrastructure.github.etag_adapter import ETagRequestAdapter
from prdiffer.infrastructure.github.client_models import (
    GITHUB_API_EXCEPTIONS,
    DEFAULT_FILE_CONTENT_CACHE_MAX_SIZE,
    DEFAULT_FILE_CONTENT_CACHE_TTL,
)
from prdiffer.infrastructure.github.client_operations import GitHubAPIClientOperationsMixin


class GitHubAPIClient(GitHubAPIClientOperationsMixin, GitHubAPIServiceInterface):
    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: int = 30,
        retry_on_404: bool = False,
        retry_on_403: bool = True,
        retry_on_500: bool = True,
        retry_log_level: str = "DEBUG",
        permanent_failure_log_level: str = "INFO",
        circuit_breaker_enabled: bool = True,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_timeout: float = 60.0,
        adaptive_retry_enabled: bool = True,
        max_adaptive_delay: float = 30.0,
        rate_limit_remaining_threshold: int = 1,
        rate_limit_reset_buffer: float = 1.0,
        secondary_rate_limit_backoff: float = 60.0,
        api_health_tracking: bool = True,
        context_aware_retry: bool = True,
        use_advanced_retry: bool = True,
        max_concurrent: int = 4,
        logger: "ConsoleLogger | None" = None,
        file_content_cache_max_size: int = DEFAULT_FILE_CONTENT_CACHE_MAX_SIZE,
        file_content_cache_ttl: int = DEFAULT_FILE_CONTENT_CACHE_TTL,
        max_file_size_bytes: int = 10485760,  # 10MB default - DoS prevention
        parallel_file_fetch_enabled: bool | None = None,
    ):
        self._github_client: Github | None = None
        self._logger = logger or get_logger()

        self._cache_max_size = file_content_cache_max_size
        self._cache_ttl = file_content_cache_ttl
        self._max_file_size_bytes = max_file_size_bytes

        # Prefer constructor injection; fall back to settings for legacy callers.
        if parallel_file_fetch_enabled is None:
            settings = get_settings_service()
            self._parallel_file_fetch_enabled = bool(settings.get("performance.parallel_file_fetch_enabled", True))
        else:
            self._parallel_file_fetch_enabled = parallel_file_fetch_enabled

        if use_advanced_retry:
            self._retry_handler = get_advanced_retry_handler(
                max_retries=max_retries,
                retry_delay=retry_delay,
                retry_on_404=retry_on_404,
                retry_on_403=retry_on_403,
                retry_on_500=retry_on_500,
                retry_log_level=retry_log_level,
                permanent_failure_log_level=permanent_failure_log_level,
                circuit_breaker_enabled=circuit_breaker_enabled,
                circuit_breaker_failure_threshold=circuit_breaker_failure_threshold,
                circuit_breaker_timeout=circuit_breaker_timeout,
                adaptive_retry_enabled=adaptive_retry_enabled,
                max_adaptive_delay=max_adaptive_delay,
                rate_limit_remaining_threshold=rate_limit_remaining_threshold,
                rate_limit_reset_buffer=rate_limit_reset_buffer,
                secondary_rate_limit_backoff=secondary_rate_limit_backoff,
                api_health_tracking=api_health_tracking,
                context_aware_retry=context_aware_retry,
                logger=None,
            )
        else:
            self._retry_handler = get_retry_handler(
                max_retries=max_retries,
                retry_delay=retry_delay,
                retry_on_404=retry_on_404,
                retry_on_403=retry_on_403,
                retry_on_500=retry_on_500,
                retry_log_level=retry_log_level,
                permanent_failure_log_level=permanent_failure_log_level,
                rate_limit_remaining_threshold=rate_limit_remaining_threshold,
                rate_limit_reset_buffer=rate_limit_reset_buffer,
                secondary_rate_limit_backoff=secondary_rate_limit_backoff,
            )

        self._file_content_cache: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0
        self._cache_evictions_ttl = 0
        self._cache_evictions_size = 0

        self._async_executor = AsyncParallelExecutor(
            max_concurrent=max_concurrent,
            error_strategy=ErrorStrategy.IGNORE,
            logger=self._logger,
        )

        self._etag_request_adapter = ETagRequestAdapter(
            cache_service=None,
            enabled=True,
            etag_ttl=self._cache_ttl,
            etag_cache_size=self._cache_max_size,
            logger=self._logger,
        )

    def initialize_client(self, github_token: str | None = None, timeout: int = 30) -> None:
        if github_token:
            auth = Token(github_token)
            self._github_client = Github(auth=auth, timeout=timeout)
        else:
            self._github_client = Github(timeout=timeout)

    def get_repository(self, repo_full_name: str) -> Repository | None:
        if not self._github_client:
            raise PRDifferException(
                "GitHub client not initialized. Call initialize_client() before using get_repository().",
                error_code=E5009_CONFIGURATION_ERROR,
            )

        try:
            pygithub_repo = self._retry_handler.execute_with_retry(
                self._github_client.get_repo,
                repo_full_name,
                context=OperationContext.REPOSITORY_ACCESS,
            )
            if pygithub_repo:
                return map_pygithub_repository_to_domain(pygithub_repo)
            return None
        except GITHUB_API_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error(f"Failed to get repository {repo_full_name}", extra=sanitized)
            return None

    def _get_pygithub_repository(self, repo_full_name: str) -> PyGithubRepository | None:
        if not self._github_client:
            raise PRDifferException("GitHub client not initialized.", error_code=E5009_CONFIGURATION_ERROR)

        try:
            result = self._retry_handler.execute_with_retry(
                self._github_client.get_repo,
                repo_full_name,
                context=OperationContext.REPOSITORY_ACCESS,
            )
            return cast(PyGithubRepository | None, result)
        except GITHUB_API_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error(f"Failed to get repository {repo_full_name}", extra=sanitized)
            return None

    def get_pull_request(self, repo_full_name: str, pr_number: int) -> PullRequest | None:
        if not self._github_client:
            raise PRDifferException(
                "GitHub client not initialized. Call initialize_client() before using get_pull_request().",
                error_code=E5009_CONFIGURATION_ERROR,
            )

        try:
            pygithub_repo = self._retry_handler.execute_with_retry(
                self._github_client.get_repo,
                repo_full_name,
                context=OperationContext.REPOSITORY_ACCESS,
            )
            if not pygithub_repo:
                return None

            pygithub_pr = self._retry_handler.execute_with_retry(pygithub_repo.get_pull, pr_number, context=OperationContext.PULL_REQUEST)
            if pygithub_pr:
                return map_pygithub_pr_to_domain(pygithub_pr)
            return None
        except GITHUB_API_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error(f"Failed to get pull request #{pr_number}", extra=sanitized)
            return None

    def _get_pygithub_pull_request(self, pygithub_repo: PyGithubRepository, pr_number: int) -> PyGithubPullRequest | None:
        try:
            result = self._retry_handler.execute_with_retry(pygithub_repo.get_pull, pr_number, context=OperationContext.PULL_REQUEST)
            return cast(PyGithubPullRequest | None, result)
        except GITHUB_API_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error(f"Failed to get pull request #{pr_number}", extra=sanitized)
            return None


def get_github_api_client(
    max_retries: int = 3,
    retry_delay: float = 1.0,
    timeout: int = 30,
    retry_on_404: bool = False,
    retry_on_403: bool = True,
    retry_on_500: bool = True,
    retry_log_level: str = "DEBUG",
    permanent_failure_log_level: str = "INFO",
    circuit_breaker_enabled: bool = True,
    circuit_breaker_failure_threshold: int = 5,
    circuit_breaker_timeout: float = 60.0,
    adaptive_retry_enabled: bool = True,
    max_adaptive_delay: float = 30.0,
    rate_limit_remaining_threshold: int | None = None,
    rate_limit_reset_buffer: float | None = None,
    secondary_rate_limit_backoff: float | None = None,
    api_health_tracking: bool = True,
    context_aware_retry: bool = True,
    use_advanced_retry: bool = True,
) -> GitHubAPIClient:
    if rate_limit_remaining_threshold is None or rate_limit_reset_buffer is None or secondary_rate_limit_backoff is None:
        from prdiffer.infrastructure.settings import get_settings_service

        settings_service = get_settings_service()
        if rate_limit_remaining_threshold is None:
            rate_limit_remaining_threshold = int(settings_service.get("github.retry.rate_limit_remaining_threshold", 1))
        if rate_limit_reset_buffer is None:
            rate_limit_reset_buffer = float(settings_service.get("github.retry.rate_limit_reset_buffer", 1.0))
        if secondary_rate_limit_backoff is None:
            secondary_rate_limit_backoff = float(settings_service.get("github.retry.secondary_rate_limit_backoff", 60.0))

    return GitHubAPIClient(
        max_retries=max_retries,
        retry_delay=retry_delay,
        timeout=timeout,
        retry_on_404=retry_on_404,
        retry_on_403=retry_on_403,
        retry_on_500=retry_on_500,
        retry_log_level=retry_log_level,
        permanent_failure_log_level=permanent_failure_log_level,
        circuit_breaker_enabled=circuit_breaker_enabled,
        circuit_breaker_failure_threshold=circuit_breaker_failure_threshold,
        circuit_breaker_timeout=circuit_breaker_timeout,
        adaptive_retry_enabled=adaptive_retry_enabled,
        max_adaptive_delay=max_adaptive_delay,
        rate_limit_remaining_threshold=rate_limit_remaining_threshold,
        rate_limit_reset_buffer=rate_limit_reset_buffer,
        secondary_rate_limit_backoff=secondary_rate_limit_backoff,
        api_health_tracking=api_health_tracking,
        context_aware_retry=context_aware_retry,
        use_advanced_retry=use_advanced_retry,
    )
