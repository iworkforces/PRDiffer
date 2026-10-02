"""GitHub strict full-diff service backing the session-capable PR diff reader."""

import os
from typing import cast, TYPE_CHECKING

from github import GithubException

if TYPE_CHECKING:
    from github.Repository import Repository
    from github.PullRequest import PullRequest
    from prdiffer.domain.interfaces.pr_diff_reader import PRDiffReadSessionInterface, PRDiffSnapshot
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.file_patch import FilePatchInfo
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.error_codes import E5003_DIFF_GENERATION_ERROR
from prdiffer.domain.exceptions import DiffGenerationError, FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.infrastructure.github.client import GitHubAPIClient
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.github.file_processor import FileProcessor
from prdiffer.infrastructure.logging.console_logger import get_logger
from prdiffer.infrastructure.logging.exception_utils import (
    sanitize_exception_for_logging,
)
from prdiffer.infrastructure.settings import get_settings_service
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


class GitHubPRDiffService:
    """GitHub strict full-diff service exposed through request-local sessions.

    ``open_pr_diff_session`` captures one immutable snapshot; the session then
    calls ``_generate_diff_content`` and ``_build_pr_diff_strict`` against it.
    """

    def __init__(
        self,
        *,
        diff_generator: DiffGenerator,
        file_processor: FileProcessor,
        github_api_client: GitHubAPIClient | None = None,
        logger: LoggerServiceInterface | None = None,
        max_total_chars: int | None = None,
        github_timeout_seconds: int | None = None,
        pr_diff_request_timeout_seconds: float | None = None,
    ):
        self._github_api: GitHubAPIClient = github_api_client or GitHubAPIClient()
        self._logger = logger or get_logger()

        config = get_settings_service().get_github_config()

        github_token = os.getenv("GITHUB_TOKEN")
        timeout = int(github_timeout_seconds if github_timeout_seconds is not None else config.timeout)

        self._github_api.initialize_client(github_token=github_token, timeout=timeout)

        self._diff_generator = diff_generator
        self._file_processor = file_processor

        self._diff_max_total_chars = int(max_total_chars if max_total_chars is not None else config.max_total_chars)
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
    ) -> "PRDiffReadSessionInterface":
        """Open a request-local GitHub session."""
        return await self._get_session_reader().open_pr_diff_session(repo_owner, repo_name, pr_number, base_url=base_url)

    def _build_pr_diff_strict(self, file_patches: list[FilePatchInfo]) -> PRDiff:
        """Build PRDiff from ordered full-context generation after size checks."""
        if not file_patches:
            return PRDiff(files=())

        try:
            generated = self._diff_generator.generate_ordered_file_diffs(file_patches)
        except FullDiffIncompleteError:
            raise
        except Exception as exc:
            raise DiffGenerationError(
                "Unexpected algorithm/runtime error during full-context generation",
                error_code=E5003_DIFF_GENERATION_ERROR,
            ) from exc

        if len(generated) != len(file_patches):
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.DIFF_GENERATION_FAILED,
                message=f"Generator returned {len(generated)} results for {len(file_patches)} files",
                observed=len(generated),
                limit=len(file_patches),
            )
        responses: list[FileDiffResponse] = []
        diffs: list[str] = []
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

        assert_aggregate_within_limit(diffs, self._diff_max_total_chars)
        return PRDiff(files=tuple(responses))

    def _generate_diff_content(
        self,
        repository: "Repository",
        pull_request: "PullRequest",
        *,
        snapshot: "PRDiffSnapshot",
    ) -> list[FilePatchInfo]:
        """Generate diff content for a pull request against an immutable snapshot.

        Head/merge-base and the authoritative changed-file count come from the
        snapshot capture — never from re-reading mutable PR tip fields.
        """
        try:
            github_files = pull_request.get_files()
            selected_files = prepare_selected_inventory(
                authoritative_changed_files=snapshot.authoritative_changed_files,
                provider_files=github_files,
                is_valid_file=self._file_processor._pattern_matcher.is_valid_file,
                max_files_allowed=self._file_processor.max_files_allowed,
            )
            if not selected_files:
                return []

            return self._file_processor.process_files_to_patches(
                list(selected_files),
                repository,
                snapshot.head_sha,
                snapshot.merge_base_sha,
            )

        except FullDiffIncompleteError:
            raise
        except PR_SERVICE_EXCEPTIONS as e:
            exc = cast(Exception, e)
            sanitized = sanitize_exception_for_logging(exc)
            self._logger.error("Failed to generate diff content", extra=sanitized)
            # Fail closed — never empty success.
            raise
