"""GitHub repository adapter for PR write operations (approve, update description).

PR operations are in github_repository_operations.py.
Utility helpers are in github_repository_utils.py.
"""

from __future__ import annotations

import os
from github.Repository import Repository
from github.PullRequest import PullRequest
from github.GithubException import (
    GithubException,
    UnknownObjectException,
    RateLimitExceededException,
)
import asyncer
from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.domain.error_codes import E5009_CONFIGURATION_ERROR
from prdiffer.infrastructure.settings import SettingsService, get_settings_service
from prdiffer.infrastructure.logging.console_logger import get_logger
from prdiffer.infrastructure.logging.exception_utils import (
    sanitize_exception_for_logging,
)
from prdiffer.infrastructure.security.input_validator import InputValidator

from prdiffer.infrastructure.github.client import get_github_api_client

from prdiffer.infrastructure.github_repository_operations import GitHubPROperationsMixin


class GitHubPRDiffRepository(GitHubPROperationsMixin, PRDiffRepositoryInterface):
    """GitHub pull request adapter for approve and description updates.

    Attributes:
        repo_owner: Repository owner/organization name
        repo_name: Repository name
        pr_number: Pull request number
    """

    def __init__(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        github_token: str | None = None,
        settings_service: SettingsService | None = None,
        logger: LoggerServiceInterface | None = None,
        input_validator: InputValidator | None = None,
    ) -> None:
        """Initialize GitHub repository with repository details and optional authentication.

        Args:
            repo_owner: The owner/organization of repository
            repo_name: The name of repository
            pr_number: The pull request number
            github_token: GitHub personal access token. If not provided,
                         uses GITHUB_TOKEN environment variable or anonymous access.
            settings_service: Optional settings service for DI
            logger: Optional logger service for DI
            input_validator: Optional input validator for DI
        """
        self._repo_owner = repo_owner
        self._repo_name = repo_name
        self._pr_number = pr_number

        self.settings_service: SettingsService = settings_service or get_settings_service()
        self._logger = logger or get_logger()
        self._input_validator = input_validator or InputValidator()

        config = self.settings_service.get_github_config()

        # Priority: parameter > GITHUB_TOKEN environment variable
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        self.timeout = config.timeout

        self._github_api_client = get_github_api_client(
            max_retries=config.max_retries,
            retry_delay=config.retry_delay,
            timeout=config.timeout,
            retry_on_404=config.retry_on_404,
            retry_on_403=config.retry_on_403,
            retry_on_500=config.retry_on_500,
            retry_log_level=config.retry_log_level,
            permanent_failure_log_level=config.permanent_failure_log_level,
            circuit_breaker_enabled=config.circuit_breaker_enabled,
            circuit_breaker_failure_threshold=config.circuit_breaker_failure_threshold,
            circuit_breaker_timeout=float(config.circuit_breaker_timeout),
            adaptive_retry_enabled=config.adaptive_retry_enabled,
            max_adaptive_delay=config.max_adaptive_delay,
            api_health_tracking=config.api_health_tracking,
            context_aware_retry=config.context_aware_retry,
        )

        self._repository: Repository | None = None
        self._pull_request: PullRequest | None = None
        self._initialized: bool = False

    @property
    def repo_owner(self) -> str:
        """Repository owner/organization name."""
        return self._repo_owner

    @property
    def repo_name(self) -> str:
        """Repository name."""
        return self._repo_name

    @property
    def pr_number(self) -> int:
        """Pull request number."""
        return self._pr_number

    async def _initialize_github_objects(self):
        """Lazy initialization of GitHub client, repository, and PR objects."""
        if self._initialized:
            return

        self._github_api_client.initialize_client(github_token=self.github_token, timeout=self.timeout)

        repo_full_name = f"{self._repo_owner}/{self._repo_name}"

        try:
            self._repository = await asyncer.asyncify(self._github_api_client._get_pygithub_repository)(repo_full_name)
        except (UnknownObjectException, RateLimitExceededException) as e:
            sanitized = sanitize_exception_for_logging(e)
            self._logger.warning(f"Repository not accessible: {repo_full_name}", extra=sanitized)
            raise PRDifferException(
                f"Failed to initialize repository {repo_full_name} - repository may not exist or access may be denied",
                error_code=E5009_CONFIGURATION_ERROR,
            ) from e
        except GithubException as e:
            sanitized = sanitize_exception_for_logging(e)
            self._logger.error(
                f"GitHub API error accessing repository {repo_full_name}",
                extra=sanitized,
            )
            raise PRDifferException(
                f"GitHub API error accessing repository {repo_full_name}",
                error_code=E5009_CONFIGURATION_ERROR,
            ) from e

        try:
            if self._repository is None:
                raise PRDifferException(
                    f"Repository {repo_full_name} is not initialized",
                    error_code=E5009_CONFIGURATION_ERROR,
                )
            self._pull_request = await asyncer.asyncify(self._github_api_client._get_pygithub_pull_request)(self._repository, self._pr_number)
        except (UnknownObjectException, RateLimitExceededException) as e:
            sanitized = sanitize_exception_for_logging(e)
            self._logger.warning(
                f"Pull request #{self._pr_number} not accessible in {repo_full_name}",
                extra=sanitized,
            )
            raise PRDifferException(
                f"Failed to initialize pull request #{self._pr_number} for repository {repo_full_name} - pull request may not exist or be inaccessible",
                error_code=E5009_CONFIGURATION_ERROR,
            ) from e
        except GithubException as e:
            sanitized = sanitize_exception_for_logging(e)
            self._logger.error(
                f"GitHub API error fetching pull request #{self._pr_number}",
                extra=sanitized,
            )
            raise RuntimeError(f"GitHub API error fetching pull request #{self._pr_number}") from e

        self._initialized = True
