"""GitHub repository implementation for PR diff data retrieval (Refactored).

This is the refactored version using composition with extracted components.
PR operations are in github_repository_operations.py.
Utility helpers are in github_repository_utils.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from github.File import File

import os
from github.Repository import Repository
from github.PullRequest import PullRequest
from github.GithubException import (
    GithubException,
    UnknownObjectException,
    RateLimitExceededException,
)
import asyncer
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.file_diff_response import FileDiffResponse
from prdiffer.domain.repositories.pr_diff_repository import PRDiffRepositoryInterface
from prdiffer.domain.services.logger import LoggerServiceInterface, LogLevel
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.domain.error_codes import E5009_CONFIGURATION_ERROR
from prdiffer.domain.config.github_config import DEFAULT_MAX_TOTAL_CHARS
from prdiffer.infrastructure.settings import SettingsService, get_settings_service
from prdiffer.infrastructure.logging.console_logger import get_logger
from prdiffer.infrastructure.logging.exception_utils import (
    sanitize_exception_for_logging,
)
from prdiffer.infrastructure.security.input_validator import InputValidator

from prdiffer.infrastructure.github.client import get_github_api_client
from prdiffer.infrastructure.github.file_processor import get_file_processor
from prdiffer.infrastructure.github.diff_generator import get_diff_generator
from prdiffer.infrastructure.github.inventory import prepare_selected_inventory
from prdiffer.infrastructure.utils.pattern_matcher import get_pattern_matcher
from prdiffer.infrastructure.utils.diff_utils import get_diff_utils
from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService

from prdiffer.infrastructure.github_repository_operations import GitHubPROperationsMixin
from prdiffer.infrastructure.github_repository_utils import (
    log_filtered_files,
    sanitize_filename_for_logging,
)


class GitHubPRDiffRepository(GitHubPROperationsMixin, PRDiffRepositoryInterface):
    """GitHub repository implementation for PR diff data retrieval.

    This refactored class uses composition with extracted components for
    better separation of concerns and maintainability.

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

        github_settings = self.settings_service.get_github_settings()
        app_settings = self.settings_service.get_app_settings()

        # Priority: parameter > GITHUB_TOKEN environment variable
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")

        self.rate_limit = github_settings.get("rate_limit", 5000)
        self.timeout = github_settings.get("timeout", 30)
        self.max_retries = github_settings.get("max_retries", 3)
        self.retry_delay = github_settings.get("retry_delay", 1)
        self.ignore_patterns = github_settings.get("ignore_patterns", [])
        self.valid_extensions = github_settings.get("valid_extensions", [])
        self.max_files_allowed = app_settings.get("max_files_allowed", 50)

        self.retry_on_404 = github_settings.get("retry_on_404", False)
        self.retry_on_403 = github_settings.get("retry_on_403", True)
        self.retry_on_500 = github_settings.get("retry_on_500", True)
        self.retry_log_level = github_settings.get("retry_log_level", "DEBUG")
        self.permanent_failure_log_level = github_settings.get("permanent_failure_log_level", "INFO")

        self.circuit_breaker_enabled = github_settings.get("circuit_breaker_enabled", True)
        self.circuit_breaker_failure_threshold = github_settings.get("circuit_breaker_failure_threshold", 5)
        self.circuit_breaker_timeout = github_settings.get("circuit_breaker_timeout", 60.0)
        self.adaptive_retry_enabled = github_settings.get("adaptive_retry_enabled", True)
        self.max_adaptive_delay = github_settings.get("max_adaptive_delay", 30.0)
        self.api_health_tracking = github_settings.get("api_health_tracking", True)
        self.context_aware_retry = github_settings.get("context_aware_retry", True)
        self.use_advanced_retry = github_settings.get("use_advanced_retry", True)

        self.diff_parallel_enabled = github_settings.get("diff_parallel_enabled", True)
        self.diff_parallel_threshold = github_settings.get("diff_parallel_threshold", 3)
        self.diff_max_workers = github_settings.get("diff_max_workers", 4)
        self.diff_worker_timeout = github_settings.get("diff_worker_timeout", 30.0)

        self.file_parallel_threshold = self.settings_service.get("file_processing.parallel_fetch_threshold", 10)
        self.file_parallel_workers = self.settings_service.get("file_processing.concurrent_downloads", 3)

        self._parallel_diff_generation_enabled = self.settings_service.get("performance.parallel_diff_generation_enabled", True)
        self._diff_truncate_enabled = self.settings_service.get("diff.truncate_enabled", False)
        get_github_config = getattr(self.settings_service, "get_github_config", None)
        if callable(get_github_config):
            self._diff_max_total_chars = int(get_github_config().max_total_chars)
        else:
            self._diff_max_total_chars = int(self.settings_service.get("diff.max_total_chars", DEFAULT_MAX_TOTAL_CHARS))
        self._diff_truncation_notice = self.settings_service.get("diff.truncation_notice", "[DIFF TRUNCATED]")

        self._github_api_client = get_github_api_client(
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            timeout=self.timeout,
            retry_on_404=self.retry_on_404,
            retry_on_403=self.retry_on_403,
            retry_on_500=self.retry_on_500,
            retry_log_level=self.retry_log_level,
            permanent_failure_log_level=self.permanent_failure_log_level,
            # Phase 3 parameters
            circuit_breaker_enabled=self.circuit_breaker_enabled,
            circuit_breaker_failure_threshold=self.circuit_breaker_failure_threshold,
            circuit_breaker_timeout=self.circuit_breaker_timeout,
            adaptive_retry_enabled=self.adaptive_retry_enabled,
            max_adaptive_delay=self.max_adaptive_delay,
            api_health_tracking=self.api_health_tracking,
            context_aware_retry=self.context_aware_retry,
            use_advanced_retry=self.use_advanced_retry,
        )

        self._pattern_matcher = get_pattern_matcher(ignore_patterns=self.ignore_patterns, valid_extensions=self.valid_extensions)

        self._diff_utils = get_diff_utils()

        self._file_processor = get_file_processor(
            github_api_service=self._github_api_client,
            pattern_matcher=self._pattern_matcher,
            diff_utils=self._diff_utils,
            max_files_allowed=self.max_files_allowed,
            parallel_fetch_threshold=self.file_parallel_threshold,
            max_parallel_workers=self.file_parallel_workers,
        )

        if self._parallel_diff_generation_enabled and self.diff_parallel_enabled:
            from prdiffer.infrastructure.utils.parallel.executor import AsyncParallelExecutor
            from prdiffer.infrastructure.utils.parallel.results import ErrorStrategy

            self._parallel_executor = AsyncParallelExecutor(
                max_concurrent=self.diff_max_workers,
                error_strategy=ErrorStrategy.IGNORE,
            )
            self._logger.info(f"Parallel diff generation enabled (max_concurrent={self.diff_max_workers}, threshold={self.diff_parallel_threshold} files)")
        else:
            self._parallel_executor = None

        self._diff_generator = get_diff_generator(
            diff_utils=self._diff_utils,
            parallel_executor=self._parallel_executor,
            parallel_enabled=self.diff_parallel_enabled,
            parallel_threshold=self.diff_parallel_threshold,
        )

        self._repository: Repository | None = None
        self._pull_request: PullRequest | None = None
        self._initialized: bool = False

    async def initialize(self) -> None:
        """Initialize the repository connection.

        This method sets up the GitHub client and validates repository access.

        Raises:
            RuntimeError: If the repository is not accessible
        """
        await self._initialize_github_objects()
        if not self._initialized:
            raise PRDifferException(
                f"Failed to initialize repository {self._repo_owner}/{self._repo_name}",
                error_code=E5009_CONFIGURATION_ERROR,
            )

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

    async def get_latest_commit_sha(self) -> str:
        """Get the latest head commit SHA for the pull request.

        Returns:
            str: The latest head commit SHA

        Raises:
            RuntimeError: If GitHub objects failed to initialize
            ValueError: If pull request cannot be refreshed
        """
        return await self._get_latest_commit_sha_sync()

    async def get_pr_diff(self) -> PRDiff:
        """Fetch PR diff information from GitHub.

        Returns:
            PRDiff: A PRDiff object containing complete PR information including:
                - PR number, repository details
                - Diff content with full file context
                - Base and head commit SHAs
                - File change statistics (changed files, additions, deletions)
                - Commit messages

        Raises:
            RuntimeError: If GitHub objects failed to initialize
        """
        return await self._get_pr_diff_sync()

    async def _get_latest_commit_sha_sync(self) -> str:
        await self._initialize_github_objects()

        if self._repository is None:
            raise RuntimeError(f"Failed to initialize repository {self._repo_owner}/{self._repo_name} - GitHub objects may not have been properly initialized")
        if self._pull_request is None:
            raise RuntimeError(f"Failed to initialize pull request #{self._pr_number} - GitHub objects may not have been properly initialized")

        repository = self._repository
        self._pull_request = await asyncer.asyncify(self._github_api_client._get_pygithub_pull_request)(repository, self._pr_number)

        if self._pull_request is None:
            raise ValueError(f"Failed to refresh pull request #{self._pr_number} - it may have been deleted or become inaccessible")

        return self._pull_request.head.sha

    async def _get_pr_diff_sync(self) -> PRDiff:
        await self._initialize_github_objects()

        if self._repository is None:
            raise RuntimeError(f"Failed to initialize repository {self._repo_owner}/{self._repo_name} - GitHub objects may not have been properly initialized")
        if self._pull_request is None:
            raise RuntimeError(f"Failed to initialize pull request #{self._pr_number} - GitHub objects may not have been properly initialized")

        base_sha, head_sha = await self._get_merge_base_commits()

        # Materialize PaginatedList once, validate authoritative inventory, then admit selected files.
        pr_files_paginated = await self._file_processor.get_pr_files(self._pull_request)
        filtered_files = prepare_selected_inventory(
            authoritative_changed_files=None,
            provider_files=pr_files_paginated,
            is_valid_file=self._file_processor._pattern_matcher.is_valid_file,
            max_files_allowed=self._file_processor.max_files_allowed,
            pull_request=self._pull_request,
        )
        pr_files = list(pr_files_paginated) if not isinstance(pr_files_paginated, list) else pr_files_paginated
        if len(filtered_files) != len(pr_files):
            self._log_filtered_files(list(pr_files), list(filtered_files))

        diff_files = self._file_processor.process_files_to_patches(filtered_files, self._repository, head_sha, base_sha)

        service = GitHubPRDiffService(
            github_api_client=self._github_api_client,
            diff_generator=self._diff_generator,
            file_processor=self._file_processor,
            logger=self._logger,
        )
        file_responses: list[FileDiffResponse] = [service._convert_file_patch_info_to_response(file_patch) for file_patch in diff_files]

        self._logger.info(f"Generated diff content for {len(file_responses)} files")

        if self._logger.should_log(LogLevel.DEBUG):
            safe_diff_preview = self._input_validator.sanitize_for_logging(
                f"Files: {len(file_responses)}",
                max_length=1000,
            )
            self._logger.debug(f"Diff content preview:\n{safe_diff_preview}")

        return PRDiff(files=tuple(file_responses))

    async def _get_merge_base_commits(self) -> tuple[str, str]:
        """Get base and head commit SHAs, using merge base for accurate comparison.

        Returns:
            tuple: (base_sha, head_sha) where base_sha is the merge base commit

        Raises:
            RuntimeError: If GitHub objects are not properly initialized
        """
        if self._repository is None:
            raise RuntimeError(f"Repository {self._repo_owner}/{self._repo_name} not initialized")
        if self._pull_request is None:
            raise RuntimeError(f"Pull request #{self._pr_number} not initialized")

        repository = self._repository
        base_sha_ref = self._pull_request.base.sha
        head_sha_ref = self._pull_request.head.sha

        try:
            compare = await asyncer.asyncify(repository.compare)(base_sha_ref, head_sha_ref)
            merge_base_commit = compare.merge_base_commit
            if merge_base_commit is None or not getattr(merge_base_commit, "sha", None):
                raise RuntimeError("Compare response missing merge_base_commit.sha")
            base_sha = merge_base_commit.sha
        except (UnknownObjectException, RateLimitExceededException, GithubException) as e:
            sanitized = sanitize_exception_for_logging(e)
            self._logger.error(
                "Could not determine merge base (no base-tip fallback)",
                extra=sanitized,
            )
            raise
        except RuntimeError:
            raise

        if base_sha != self._pull_request.base.sha:
            self._logger.info(f"Using merge base commit {base_sha} instead of base commit {self._pull_request.base.sha}")

        head_sha = self._pull_request.head.sha
        return base_sha, head_sha

    def _sanitize_filename_for_logging(self, filename: str) -> str:
        """Sanitize a filename for safe logging."""
        return sanitize_filename_for_logging(self._input_validator, filename)

    def _log_filtered_files(self, original_files: list[File], filtered_files: list[File]) -> None:
        """Log information about filtered files with sanitized names."""
        log_filtered_files(self._logger, self._input_validator, original_files, filtered_files)


_repository_cache: dict[str, "GitHubPRDiffRepository"] = {}


def get_github_repository(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    github_token: str | None = None,
    settings_service: SettingsService | None = None,
    logger: LoggerServiceInterface | None = None,
    input_validator: InputValidator | None = None,
) -> GitHubPRDiffRepository:
    """Get a GitHub repository instance (singleton pattern per repository/PR).

    This function provides a singleton pattern for GitHubPRDiffRepository instances
    to avoid creating multiple instances for the same repository and PR.

    Args:
        repo_owner: Repository owner/organization
        repo_name: Repository name
        pr_number: Pull request number
        github_token: GitHub personal access token (optional)
        settings_service: Optional settings service for DI
        logger: Optional logger service for DI
        input_validator: Optional input validator for DI

    Returns:
        GitHubPRDiffRepository: The repository instance
    """
    global _repository_cache

    cache_key = f"{repo_owner}/{repo_name}/pr/{pr_number}"
    if github_token:
        cache_key = f"{cache_key}/token"

    if cache_key not in _repository_cache:
        _repository_cache[cache_key] = GitHubPRDiffRepository(
            repo_owner,
            repo_name,
            pr_number,
            github_token,
            settings_service,
            logger,
            input_validator,
        )

    return _repository_cache[cache_key]
