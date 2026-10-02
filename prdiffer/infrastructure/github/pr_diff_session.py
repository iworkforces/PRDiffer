"""Request-local GitHub PR diff session with anyio thread isolation.

Synchronous PyGithub work runs via ``anyio.to_thread.run_sync`` with a
serialized CapacityLimiter (capacity 1 when parallel fetch is disabled).
"""

from __future__ import annotations

import time
from typing import Any, Callable, ParamSpec, TypeVar

import anyio
import anyio.to_thread
from github import Github, GithubException
from github.PullRequest import PullRequest as PyGithubPullRequest
from github.Repository import Repository as PyGithubRepository

from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import (
    StrictPRDiffCacheIdentity,
    github_full_diff_v3_identity,
)
from prdiffer.domain.error_codes import E5002_GITHUB_API_ERROR, E5004_TIMEOUT_ERROR, E5009_CONFIGURATION_ERROR
from prdiffer.domain.exceptions import (
    FullDiffIncompleteError,
    FullDiffIncompleteReason,
    GitHubAPIError,
    PRDifferException,
    TimeoutError as DomainTimeoutError,
)
from prdiffer.domain.interfaces.pr_diff_reader import (
    PRDiffReadSessionInterface,
    PRDiffSnapshot,
    require_changed_files_count,
    require_git_object_sha,
)
from prdiffer.infrastructure.logging.console_logger import get_logger
from prdiffer.infrastructure.logging.exception_utils import sanitize_exception_for_logging
from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService


_P = ParamSpec("_P")
_T = TypeVar("_T")


def _resolve_merge_base_sha(repository: PyGithubRepository, base_tip_sha: str, head_sha: str) -> str:
    """Resolve merge-base via Compare API; never fall back to base tip."""
    try:
        compare = repository.compare(base_tip_sha, head_sha)
    except GithubException as exc:
        sanitized = sanitize_exception_for_logging(exc)
        raise GitHubAPIError(
            "Failed to resolve GitHub merge base via compare",
            status_code=getattr(exc, "status", None),
            error_code=E5002_GITHUB_API_ERROR,
            details={"status": sanitized.get("status")},
        ) from exc
    except (TimeoutError, ConnectionError, OSError) as exc:
        sanitized = sanitize_exception_for_logging(exc)
        raise GitHubAPIError(
            "Failed to resolve GitHub merge base via compare",
            error_code=E5002_GITHUB_API_ERROR,
            details=sanitized,
        ) from exc

    merge_base_commit = getattr(compare, "merge_base_commit", None)
    raw_sha = getattr(merge_base_commit, "sha", None) if merge_base_commit is not None else None
    if not isinstance(raw_sha, str) or not raw_sha:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.INVENTORY_TRUNCATED,
            message="GitHub compare omitted merge_base_commit.sha",
        )
    try:
        return require_git_object_sha(raw_sha, field="merge_base_sha")
    except ValueError as exc:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.INVENTORY_TRUNCATED,
            message="GitHub compare returned an invalid merge_base_commit.sha",
        ) from exc


def _capture_github_snapshot(
    *,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    repository: PyGithubRepository,
    pull_request: PyGithubPullRequest,
) -> PRDiffSnapshot:
    """Capture base tip, head, authoritative count, and resolve merge-base once."""
    try:
        base_tip_sha = require_git_object_sha(getattr(pull_request.base, "sha", None), field="base_tip_sha")
        head_sha = require_git_object_sha(getattr(pull_request.head, "sha", None), field="head_sha")
        authoritative = require_changed_files_count(
            getattr(pull_request, "changed_files", None),
            field="authoritative_changed_files",
        )
    except ValueError as exc:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.INVENTORY_TRUNCATED,
            message=f"Invalid GitHub PR snapshot metadata: {exc}",
        ) from exc

    merge_base_sha = _resolve_merge_base_sha(repository, base_tip_sha, head_sha)
    return PRDiffSnapshot(
        owner=repo_owner,
        repo=repo_name,
        pr_number=pr_number,
        base_tip_sha=base_tip_sha,
        merge_base_sha=merge_base_sha,
        head_sha=head_sha,
        authoritative_changed_files=authoritative,
    )


def revalidate_github_snapshot(
    repository: PyGithubRepository,
    snapshot: PRDiffSnapshot,
) -> None:
    """Re-fetch PR metadata + merge-base; raise SNAPSHOT_CHANGED on drift.

    Base-tip alone may change without failing when merge-base, head, and count match.
    """
    try:
        pull_request = repository.get_pull(snapshot.pr_number)
    except GithubException as exc:
        sanitized = sanitize_exception_for_logging(exc)
        raise GitHubAPIError(
            "Failed to revalidate GitHub PR snapshot",
            status_code=getattr(exc, "status", None),
            error_code=E5002_GITHUB_API_ERROR,
            details={"status": sanitized.get("status")},
        ) from exc

    try:
        base_tip_sha = require_git_object_sha(getattr(pull_request.base, "sha", None), field="base_tip_sha")
        head_sha = require_git_object_sha(getattr(pull_request.head, "sha", None), field="head_sha")
        authoritative = require_changed_files_count(
            getattr(pull_request, "changed_files", None),
            field="authoritative_changed_files",
        )
    except ValueError as exc:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.SNAPSHOT_CHANGED,
            message=f"GitHub PR snapshot became invalid during revalidation: {exc}",
            observed=str(exc),
        ) from exc

    merge_base_sha = _resolve_merge_base_sha(repository, base_tip_sha, head_sha)
    if head_sha != snapshot.head_sha or merge_base_sha != snapshot.merge_base_sha or authoritative != snapshot.authoritative_changed_files:
        raise FullDiffIncompleteError(
            FullDiffIncompleteReason.SNAPSHOT_CHANGED,
            message="GitHub PR comparison snapshot changed during build",
            observed=f"head={head_sha},merge_base={merge_base_sha},count={authoritative}",
            limit=f"head={snapshot.head_sha},merge_base={snapshot.merge_base_sha},count={snapshot.authoritative_changed_files}",
        )


class GitHubPRDiffSession(PRDiffReadSessionInterface):
    """One request session: one client/repo/PR metadata lookup, always closed."""

    def __init__(
        self,
        *,
        snapshot: PRDiffSnapshot,
        github_client: Github,
        repository: PyGithubRepository,
        pull_request: PyGithubPullRequest,
        service: GitHubPRDiffService,
        limiter: anyio.CapacityLimiter,
        deadline_monotonic: float,
        logger: Any | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._github_client: Github | None = github_client
        self._repository: PyGithubRepository | None = repository
        self._pull_request: PyGithubPullRequest | None = pull_request
        self._service = service
        self._limiter = limiter
        self._deadline_monotonic = deadline_monotonic
        self._logger = logger or get_logger()
        self._closed = False
        self._metadata_lookups = 1

    @property
    def snapshot(self) -> PRDiffSnapshot:
        return self._snapshot

    @property
    def cache_identity(self) -> StrictPRDiffCacheIdentity:
        """GitHub full-diff v3 key + merge-base:head validation token."""
        snap = self._snapshot
        return github_full_diff_v3_identity(
            snap.owner,
            snap.repo,
            snap.pr_number,
            snap.merge_base_sha,
            snap.head_sha,
        )

    @property
    def metadata_lookup_count(self) -> int:
        return self._metadata_lookups

    def _remaining_budget(self) -> float:
        return self._deadline_monotonic - time.monotonic()

    def _ensure_budget(self) -> None:
        remaining = self._remaining_budget()
        if remaining <= 0:
            raise DomainTimeoutError(
                "PR diff request deadline exhausted",
                error_code=E5004_TIMEOUT_ERROR,
                details={"limit": self._deadline_monotonic},
            )

    async def _run_sync(self, func: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
        """Run blocking work on a worker thread with abandon_on_cancel=False.

        Capacity is acquired here so remaining budget can be re-checked after the
        queue wait (mirrors GitLabRuntime.run_blocking). The limiter is not also
        passed to ``run_sync`` (would double-acquire).
        """
        self._ensure_budget()

        def _call() -> _T:
            return func(*args, **kwargs)

        async with self._limiter:
            # Reject if the request waited on capacity past the deadline.
            self._ensure_budget()
            return await anyio.to_thread.run_sync(
                _call,
                abandon_on_cancel=False,
            )

    async def build_pr_diff(self) -> PRDiff:
        """Build PRDiff using the already-open session handles, then revalidate snapshot."""
        self._ensure_budget()

        def _build() -> PRDiff:
            # Reuse request-local repository/PR objects; avoid a second metadata lookup for build.
            repo = self._repository
            pr = self._pull_request
            if repo is None or pr is None:
                raise PRDifferException("Session closed", error_code=E5009_CONFIGURATION_ERROR)
            patches = self._service._generate_diff_content(repo, pr, snapshot=self._snapshot)
            result = self._service._build_pr_diff_strict(patches)
            # Re-fetch metadata + merge-base; fail closed on drift before use-case cache write.
            revalidate_github_snapshot(repo, self._snapshot)
            return result

        result = await self._run_sync(_build)
        # Post-worker budget: discard late results after the blocking worker exits.
        self._ensure_budget()
        return result

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        # PyGithub Github client has no mandatory close; drop strong refs.
        self._pull_request = None
        self._repository = None
        self._github_client = None


class GitHubSessionPRDiffReader:
    """Session-capable reader wrapping GitHubPRDiffService."""

    def __init__(
        self,
        service: GitHubPRDiffService,
        *,
        github_timeout_seconds: int = 30,
        request_timeout_seconds: float = 180.0,
        parallel_file_fetch_enabled: bool = True,
        max_concurrent: int = 4,
        logger: Any | None = None,
    ) -> None:
        self._service = service
        self._github_timeout_seconds = github_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        capacity = max_concurrent if parallel_file_fetch_enabled else 1
        self._limiter = anyio.CapacityLimiter(capacity)
        self._logger = logger or get_logger()

    async def open_pr_diff_session(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ) -> GitHubPRDiffSession:
        del base_url  # GitHub.com only; keyword accepted for protocol uniformity
        deadline = time.monotonic() + float(self._request_timeout_seconds)

        def _open() -> tuple[Github, PyGithubRepository, PyGithubPullRequest, PRDiffSnapshot]:
            client = self._service._github_api
            if getattr(client, "_github_client", None) is None:
                raise PRDifferException(
                    "GitHub client not initialized",
                    error_code=E5009_CONFIGURATION_ERROR,
                )
            gh = client._github_client
            assert gh is not None
            repo = client._get_pygithub_repository(f"{repo_owner}/{repo_name}")
            if repo is None:
                raise PRDifferException(
                    f"Repository not found: {repo_owner}/{repo_name}",
                    error_code=E5009_CONFIGURATION_ERROR,
                )
            pr = client._get_pygithub_pull_request(repo, pr_number)
            if pr is None:
                raise PRDifferException(
                    f"Pull request not found: {repo_owner}/{repo_name}#{pr_number}",
                    error_code=E5009_CONFIGURATION_ERROR,
                )
            # Merge-base is resolved before session returns (before any cache read).
            snapshot = _capture_github_snapshot(
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                repository=repo,
                pull_request=pr,
            )
            return gh, repo, pr, snapshot

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DomainTimeoutError(
                "PR diff request deadline exhausted before session open",
                error_code=E5004_TIMEOUT_ERROR,
            )

        to_thread = anyio.to_thread
        run_sync = getattr(to_thread, "run_sync")
        # Acquire capacity first so post-queue budget can reject overdue open work.
        async with self._limiter:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DomainTimeoutError(
                    "PR diff request deadline exhausted while waiting for capacity",
                    error_code=E5004_TIMEOUT_ERROR,
                )
            gh, repo, pr, snapshot = await run_sync(
                _open,
                abandon_on_cancel=False,
            )
        # Post-worker budget check: late open workers must not return after deadline.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DomainTimeoutError(
                "PR diff request deadline exhausted after session open",
                error_code=E5004_TIMEOUT_ERROR,
            )
        return GitHubPRDiffSession(
            snapshot=snapshot,
            github_client=gh,
            repository=repo,
            pull_request=pr,
            service=self._service,
            limiter=self._limiter,
            deadline_monotonic=deadline,
            logger=self._logger,
        )
