"""GitHub API client with retry and circuit breaker support."""

from typing import cast

from github import Github
from github.Auth import Token
from github.Repository import Repository as PyGithubRepository
from github.PullRequest import PullRequest as PyGithubPullRequest

from prdiffer.domain.services.github_api import GitHubAPIServiceInterface
from prdiffer.infrastructure.utils.retry.factories import get_retry_handler
from prdiffer.infrastructure.utils.retry.models import OperationContext
from prdiffer.infrastructure.logging.console_logger import ConsoleLogger, get_logger
from prdiffer.infrastructure.logging.exception_utils import (
    sanitize_exception_for_logging,
)
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.domain.error_codes import E5009_CONFIGURATION_ERROR
from prdiffer.infrastructure.github.client_models import GITHUB_API_EXCEPTIONS


class GitHubAPIClient(GitHubAPIServiceInterface):
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
        logger: "ConsoleLogger | None" = None,
    ):
        self._github_client: Github | None = None
        self._logger = logger or get_logger()

        self._retry_handler = get_retry_handler(
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
        )

    def initialize_client(self, github_token: str | None = None, timeout: int = 30) -> None:
        if github_token:
            auth = Token(github_token)
            self._github_client = Github(auth=auth, timeout=timeout)
        else:
            self._github_client = Github(timeout=timeout)

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
    )
