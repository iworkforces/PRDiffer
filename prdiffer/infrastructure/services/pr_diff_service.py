"""Infrastructure implementation of PRDiffServiceInterface using GitHub API."""

import os
from typing import Any, cast, TYPE_CHECKING

import asyncer
from github import GithubException

if TYPE_CHECKING:
    from github.Repository import Repository
    from github.PullRequest import PullRequest
    from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
from prdiffer.domain.services.pr_diff_service import PRDiffServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.infrastructure.github.client import GitHubAPIClient
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.github.file_processor import FileProcessor
from prdiffer.infrastructure.logging.console_logger import get_logger
from prdiffer.infrastructure.logging.exception_utils import (
    sanitize_exception_for_logging,
)
from prdiffer.infrastructure.security.input_validator import InputValidator
from prdiffer.infrastructure.settings import get_settings_service
from prdiffer.infrastructure.cache.cache_decorators import (
    CachingMixin,
    cached_method,
)
from prdiffer.infrastructure.github.inventory import prepare_selected_inventory
from prdiffer.infrastructure.utils.diff_limits import assert_aggregate_within_limit, assert_diff_within_limit


# Exceptions to catch in PR diff service operations
# Note: We deliberately exclude KeyboardInterrupt, SystemExit, and GeneratorExit
# to allow system-level exceptions to propagate for proper shutdown/cleanup.
PR_SERVICE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    GithubException,
    TimeoutError,
    ConnectionError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
)


class GitHubPRDiffService(CachingMixin, PRDiffServiceInterface):
    """Concrete implementation of PRDiffServiceInterface using GitHub API.

    Features:
    - PR diff retrieval with commit-based caching at use case level
    - Method-level caching with TTL for performance optimization
    - Thread-safe caching with reentrant lock protection
    """

    def __init__(
        self,
        github_api_client: GitHubAPIClient | None = None,
        diff_generator: DiffGenerator | None = None,
        file_processor: FileProcessor | None = None,
        logger: LoggerServiceInterface | None = None,
        *,
        max_total_chars: int | None = None,
        github_timeout_seconds: int | None = None,
        pr_diff_request_timeout_seconds: float | None = None,
    ):
        super().__init__(max_cache_size=1000, default_ttl=300)

        self._github_api: GitHubAPIClient = github_api_client or GitHubAPIClient()
        self._logger = logger or get_logger()

        settings_service = get_settings_service()
        config = settings_service.get_github_config()

        github_token = os.getenv("GITHUB_TOKEN")
        timeout = int(github_timeout_seconds if github_timeout_seconds is not None else config.timeout)

        self._github_api.initialize_client(github_token=github_token, timeout=timeout)

        self._diff_generator = diff_generator
        self._file_processor = file_processor

        self._diff_truncate_enabled = settings_service.get("diff.truncate_enabled", False)
        self._diff_max_total_chars = int(max_total_chars if max_total_chars is not None else config.max_total_chars)
        self._diff_truncation_notice = settings_service.get("diff.truncation_notice", "[DIFF TRUNCATED]")
        self._pr_diff_request_timeout_seconds = float(
            pr_diff_request_timeout_seconds if pr_diff_request_timeout_seconds is not None else config.pr_diff_request_timeout_seconds
        )
        self._github_timeout_seconds = timeout
        self._parallel_file_fetch_enabled = config.parallel_file_fetch_enabled
        self._max_concurrent = config.github_worker_capacity
        self._session_reader = None

    def _get_session_reader(self):
        """Lazy session-capable wrapper (structural SessionPRDiffReader)."""
        if self._session_reader is None:
            from prdiffer.infrastructure.github.pr_diff_session import GitHubSessionPRDiffReader

            self._session_reader = GitHubSessionPRDiffReader(
                self,
                github_timeout_seconds=self._github_timeout_seconds,
                request_timeout_seconds=self._pr_diff_request_timeout_seconds,
                parallel_file_fetch_enabled=self._parallel_file_fetch_enabled,
                max_concurrent=self._max_concurrent,
                logger=self._logger,
            )
        return self._session_reader

    async def open_pr_diff_session(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ):
        """Open a request-local GitHub session (enables use-case session path)."""
        return await self._get_session_reader().open_pr_diff_session(repo_owner, repo_name, pr_number, base_url=base_url)

    async def get_pr_diff(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> PRDiff | None:
        """Get PR diff data for the specified repository and PR.

        Raises:
            RepositoryNotFoundError: If repository or PR doesn't exist
            AuthenticationError: If authentication fails
            RateLimitError: If rate limit is exceeded
            ValidationError: If input parameters are invalid
        """
        if self._file_processor and hasattr(self._file_processor, "process_files_to_patches_async"):
            return await self._get_pr_diff_async_native(repo_owner, repo_name, pr_number)

        return await asyncer.asyncify(self._get_pr_diff_sync)(repo_owner, repo_name, pr_number)

    async def _get_pr_diff_async_native(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> PRDiff | None:
        """Get PR diff data using native async with parallel file processing."""
        try:
            repository = self._github_api._get_pygithub_repository(f"{repo_owner}/{repo_name}")
            if not repository:
                return None

            pull_request = self._github_api._get_pygithub_pull_request(repository, pr_number)
            if not pull_request:
                return None

            _, diff_files = await self._generate_diff_content_async(repository, pull_request)

            pr_diff = self._build_pr_diff_strict(diff_files)

            self._logger.info(
                "Generated diff content (async parallel)",
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                num_files=len(pr_diff.files),
            )

            preview = InputValidator.sanitize_for_logging(f"Files: {len(pr_diff.files)}", max_length=1000)
            self._logger.debug("Diff content preview", preview=preview)

            return pr_diff

        except FullDiffIncompleteError:
            raise
        except PR_SERVICE_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error(
                "Failed to get PR diff (async)",
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                extra=sanitized,
            )
            # Fail closed: never convert provider failures into empty/None success.
            raise

    async def _generate_diff_content_async(self, repository: Repository, pull_request: PullRequest) -> tuple[str, list[FilePatchInfo]]:
        """Generate diff content using async parallel processing.

        Returns:
            tuple[str, list[FilePatchInfo]]: Combined diff content and file metadata.
            Provider failures raise rather than returning empty success.
        """
        try:
            latest_commit_sha = pull_request.head.sha
            if not latest_commit_sha:
                raise ValueError("Pull request head SHA is missing")

            base_commit_sha = self._get_base_commit_sha(repository, pull_request)
            if not base_commit_sha:
                raise ValueError("Pull request base SHA is missing")

            github_files = pull_request.get_files()
            max_files = self._file_processor.max_files_allowed if self._file_processor is not None else 50

            def accept_all(_name: str) -> bool:
                return True

            is_valid = self._file_processor._pattern_matcher.is_valid_file if self._file_processor is not None else accept_all
            selected_files = prepare_selected_inventory(
                authoritative_changed_files=None,
                provider_files=github_files,
                is_valid_file=is_valid,
                max_files_allowed=max_files,
                pull_request=pull_request,
            )
            if not selected_files:
                return "", []

            if self._file_processor and hasattr(self._file_processor, "process_files_to_patches_async"):
                diff_files = await self._file_processor.process_files_to_patches_async(list(selected_files), repository, latest_commit_sha, base_commit_sha)
            else:
                diff_files = (
                    self._file_processor.process_files_to_patches(
                        list(selected_files),
                        repository,
                        latest_commit_sha,
                        base_commit_sha,
                    )
                    if self._file_processor
                    else self._convert_github_files_to_file_patch_info(selected_files)
                )

            if self._diff_generator and diff_files:
                extended_diffs = self._diff_generator.generate_extended_diff(diff_files)
                return "\n".join(extended_diffs), diff_files
            else:
                diff_content_parts: list[str] = []
                for file_patch in diff_files:
                    if file_patch.patch:
                        diff_content_parts.append(f"## File: {file_patch.filename}\n{file_patch.patch}")
                return "\n\n".join(diff_content_parts), diff_files

        except FullDiffIncompleteError:
            raise
        except PR_SERVICE_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error("Failed to generate diff content (async)", extra=sanitized)
            raise

    async def get_latest_commit_sha(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> str | None:
        """Get the latest head commit SHA for the pull request."""
        return await asyncer.asyncify(self._get_latest_commit_sha_sync)(repo_owner, repo_name, pr_number)

    @cached_method(ttl=300, key_prefix="pr_diff")
    def _get_pr_diff_sync(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> PRDiff | None:
        """Get PR diff data synchronously with method-level caching.

        Results cached for 5 minutes (300s). Use case-level commit-based
        caching provides additional freshness guarantees.
        """
        try:
            repository = self._github_api._get_pygithub_repository(f"{repo_owner}/{repo_name}")
            if not repository:
                return None

            pull_request = self._github_api._get_pygithub_pull_request(repository, pr_number)
            if not pull_request:
                return None

            diff_files = self._generate_diff_content(repository, pull_request)

            pr_diff = self._build_pr_diff_strict(diff_files)

            self._logger.info(
                "Generated diff content",
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                num_files=len(pr_diff.files),
            )

            preview = InputValidator.sanitize_for_logging(f"Files: {len(pr_diff.files)}", max_length=1000)
            self._logger.debug("Diff content preview", preview=preview)

            return pr_diff

        except FullDiffIncompleteError:
            raise
        except PR_SERVICE_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error(
                "Failed to get PR diff",
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                extra=sanitized,
            )
            return None

    def _get_latest_commit_sha_sync(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> str | None:
        try:
            repository = self._github_api._get_pygithub_repository(f"{repo_owner}/{repo_name}")
            if not repository:
                return None

            pull_request = self._github_api._get_pygithub_pull_request(repository, pr_number)
            if not pull_request:
                return None

            return pull_request.head.sha

        except PR_SERVICE_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error(
                "Failed to get latest commit SHA",
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                extra=sanitized,
            )
            return None

    def _convert_github_files_to_file_patch_info(self, github_files: Any) -> list[FilePatchInfo]:
        """Convert GitHub File objects to FilePatchInfo domain entities."""
        file_patch_infos: list[FilePatchInfo] = []

        for github_file in github_files:
            edit_type = self._map_github_status_to_edit_type(github_file.status)

            # Get file content if available (for now, we'll use empty strings)
            # In a full implementation, we would fetch the actual file content
            base_file = ""
            head_file = ""

            file_patch_info = FilePatchInfo(
                filename=github_file.filename,
                base_file=base_file,
                head_file=head_file,
                patch=github_file.patch or "",
                edit_type=edit_type,
                num_plus_lines=github_file.additions or 0,
                num_minus_lines=github_file.deletions or 0,
            )

            file_patch_infos.append(file_patch_info)

        return file_patch_infos

    def _map_github_status_to_edit_type(self, status: str) -> EDIT_TYPE:
        """Map GitHub file status to EDIT_TYPE enum."""
        status_mapping = {
            "added": EDIT_TYPE.ADDED,
            "removed": EDIT_TYPE.DELETED,
            "modified": EDIT_TYPE.MODIFIED,
            "renamed": EDIT_TYPE.RENAMED,
        }

        return status_mapping.get(status, EDIT_TYPE.UNKNOWN)

    def _convert_file_patch_info_to_response(self, file_patch: FilePatchInfo) -> FileDiffResponse:
        """Convert FilePatchInfo to FileDiffResponse.

        Field mapping: filename→path, edit_type→status,
        num_plus_lines→stats.additions, num_minus_lines→stats.deletions, patch→diff
        """
        stats = FileStats(additions=file_patch.num_plus_lines, deletions=file_patch.num_minus_lines)
        diff_text = file_patch.patch or ""
        # Per-file public diff character limit (max_diff_size is line-oriented for builders;
        # character budget for the public string is max_total_chars / files enforced in aggregate).
        return FileDiffResponse(
            path=file_patch.filename,
            status=file_patch.edit_type,
            stats=stats,
            diff=diff_text,
            previous_path=file_patch.old_filename,
        )

    def _build_pr_diff_strict(self, file_patches: list[FilePatchInfo]) -> PRDiff:
        """Build PRDiff from ordered full-context generation after size checks."""
        if not file_patches:
            return PRDiff(files=())

        try:
            if self._diff_generator is not None and hasattr(self._diff_generator, "generate_ordered_file_diffs"):
                generated = self._diff_generator.generate_ordered_file_diffs(file_patches)
            else:
                # Fallback: map patches without full-context regeneration.
                generated = None
        except FullDiffIncompleteError:
            raise
        except Exception as exc:
            from prdiffer.domain.error_codes import E5003_DIFF_GENERATION_ERROR
            from prdiffer.domain.exceptions import DiffGenerationError

            raise DiffGenerationError(
                "Unexpected algorithm/runtime error during full-context generation",
                error_code=E5003_DIFF_GENERATION_ERROR,
            ) from exc

        responses: list[FileDiffResponse] = []
        diffs: list[str] = []
        if generated is not None:
            if len(generated) != len(file_patches):
                raise FullDiffIncompleteError(
                    FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                    message=f"Generator returned {len(generated)} results for {len(file_patches)} files",
                    observed=len(generated),
                    limit=len(file_patches),
                )
            for item, file_patch in zip(generated, file_patches, strict=True):
                if item.path != file_patch.filename:
                    raise FullDiffIncompleteError(
                        FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                        message="Generated path identity mismatch",
                        path=item.path,
                    )
                stats = FileStats(additions=file_patch.num_plus_lines, deletions=file_patch.num_minus_lines)
                response = FileDiffResponse(
                    path=item.path,
                    status=file_patch.edit_type,
                    stats=stats,
                    diff=item.diff,
                    previous_path=item.previous_path,
                )
                assert_diff_within_limit(response.diff, self._diff_max_total_chars, path=response.path)
                responses.append(response)
                diffs.append(response.diff)
        else:
            for file_patch in file_patches:
                response = self._convert_file_patch_info_to_response(file_patch)
                assert_diff_within_limit(response.diff, self._diff_max_total_chars, path=response.path)
                responses.append(response)
                diffs.append(response.diff)

        assert_aggregate_within_limit(diffs, self._diff_max_total_chars)
        return PRDiff(files=tuple(responses))

    def _generate_diff_content(
        self,
        repository: Repository,
        pull_request: PullRequest,
        *,
        snapshot: PRDiffSnapshot | None = None,
    ) -> list[FilePatchInfo]:
        """Generate diff content for a pull request.

        When ``snapshot`` is provided (strict session path), head/merge-base and the
        authoritative changed-file count come from the immutable capture — never from
        re-reading mutable PR tip fields during generation.
        """
        try:
            if snapshot is not None:
                latest_commit_sha = snapshot.head_sha
                base_commit_sha = snapshot.merge_base_sha
                authoritative_changed_files: int | None = snapshot.authoritative_changed_files
            else:
                latest_commit_sha = pull_request.head.sha
                if not latest_commit_sha:
                    raise ValueError("Pull request head SHA is missing")
                base_commit_sha = self._get_base_commit_sha(repository, pull_request)
                if not base_commit_sha:
                    raise ValueError("Pull request base SHA is missing")
                authoritative_changed_files = None

            github_files = pull_request.get_files()
            max_files = self._file_processor.max_files_allowed if self._file_processor is not None else 50

            def accept_all(_name: str) -> bool:
                return True

            is_valid = self._file_processor._pattern_matcher.is_valid_file if self._file_processor is not None else accept_all
            selected_files = prepare_selected_inventory(
                authoritative_changed_files=authoritative_changed_files,
                provider_files=github_files,
                is_valid_file=is_valid,
                max_files_allowed=max_files,
                pull_request=pull_request,
            )
            if not selected_files:
                return []

            if self._file_processor:
                diff_files = self._file_processor.process_files_to_patches(list(selected_files), repository, latest_commit_sha, base_commit_sha)
            else:
                diff_files = self._convert_github_files_to_file_patch_info(selected_files)

            return diff_files

        except FullDiffIncompleteError:
            raise
        except PR_SERVICE_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error("Failed to generate diff content", extra=sanitized)
            # Fail closed for both session and legacy callers — never empty success.
            raise

    def _get_base_commit_sha(self, repository: Repository, pull_request: PullRequest) -> str | None:
        """Legacy non-session path: use PR base tip SHA (not Compare merge-base).

        Strict sessions must pass ``snapshot.merge_base_sha`` into
        ``_generate_diff_content`` instead of calling this helper.
        """
        try:
            base_branch: str | None = pull_request.base.sha
            if base_branch:
                return base_branch

            # Fallback: use the base branch reference
            base_ref = repository.get_git_ref(f"heads/{pull_request.base.ref}")
            if base_ref:
                base_sha: str | None = base_ref.object.sha
                return base_sha

            return None
        except PR_SERVICE_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error("Failed to get base commit SHA", extra=sanitized)
            return None

    def validate_repository_access(
        self,
        repo_owner: str,
        repo_name: str,
    ) -> bool:
        """Validate that the repository exists and is accessible."""
        try:
            repository = self._github_api.get_repository(f"{repo_owner}/{repo_name}")
            return repository is not None
        except PR_SERVICE_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error(
                "Failed to validate repository access",
                repo_owner=repo_owner,
                repo_name=repo_name,
                extra=sanitized,
            )
            return False
