"""Request-scoped GitLab strict full-diff session."""

from __future__ import annotations

import time

import gitlab

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import (
    StrictPRDiffCacheIdentity,
    gitlab_full_diff_v1_identity,
)
from prdiffer.domain.error_codes import E5004_TIMEOUT_ERROR, E5009_CONFIGURATION_ERROR
from prdiffer.domain.exceptions import PRDifferException, TimeoutError as DomainTimeoutError
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffReadSessionInterface, PRDiffSnapshot
from prdiffer.infrastructure.vcs_providers.gitlab_content import GitLabContentFetcher
from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import GitLabDiffAssembler
from prdiffer.infrastructure.vcs_providers.gitlab_inventory import admit_inventory
from prdiffer.infrastructure.vcs_providers.gitlab_models import GitLabDiffSnapshot
from prdiffer.infrastructure.vcs_providers.gitlab_operations import GitLabOperations
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import (
    GITLAB_COM_URL,
    GitLabNotFoundContext,
    GitLabNotFoundKind,
    GitLabRuntime,
    cache_host_from_base_url,
)


class GitLabPRDiffSession(PRDiffReadSessionInterface):
    """One request: pin snapshot, admit inventory, fetch content, assemble, close."""

    def __init__(
        self,
        *,
        snapshot: GitLabDiffSnapshot,
        operations: GitLabOperations,
        content_fetcher: GitLabContentFetcher,
        assembler: GitLabDiffAssembler,
        config: GitLabConfig,
        runtime: GitLabRuntime,
        deadline_monotonic: float,
        base_url: str | None = None,
    ) -> None:
        self._gitlab_snapshot = snapshot
        self._operations = operations
        self._content_fetcher = content_fetcher
        self._assembler = assembler
        self._config = config
        self._runtime = runtime
        self._deadline_monotonic = deadline_monotonic
        self._base_url = (base_url or GITLAB_COM_URL).rstrip("/")
        self._closed = False
        self._built = False
        # Domain PRDiffSnapshot uses owner/repo split; owner may be nested namespace.
        path_parts = snapshot.project_path.rsplit("/", 1)
        if len(path_parts) != 2:
            owner, repo = snapshot.project_path, ""
        else:
            owner, repo = path_parts[0], path_parts[1]
        # Domain snapshot requires tip + merge-base fields; GitLab pins base as
        # merge-base and start as base tip when both are available.
        self._snapshot = PRDiffSnapshot(
            owner=owner,
            repo=repo,
            pr_number=snapshot.iid,
            base_tip_sha=snapshot.start_sha,
            merge_base_sha=snapshot.base_sha,
            head_sha=snapshot.head_sha,
            authoritative_changed_files=len(snapshot.records),
        )
        # Port-aware host identity (e.g. gitlab.example.com:8443).
        host = cache_host_from_base_url(self._base_url)
        self._cache_identity = gitlab_full_diff_v1_identity(
            namespace=owner,
            repo=repo,
            iid=snapshot.iid,
            version_id=snapshot.version_id,
            base_sha=snapshot.base_sha,
            start_sha=snapshot.start_sha,
            head_sha=snapshot.head_sha,
            host=host,
        )

    @property
    def snapshot(self) -> PRDiffSnapshot:
        return self._snapshot

    @property
    def cache_identity(self) -> StrictPRDiffCacheIdentity:
        return self._cache_identity

    def _ensure_open(self) -> None:
        if self._closed:
            raise PRDifferException("GitLab PR diff session is closed", error_code=E5009_CONFIGURATION_ERROR)

    def _ensure_budget(self) -> None:
        if self._deadline_monotonic - time.monotonic() <= 0:
            raise DomainTimeoutError(
                "PR diff request deadline exhausted",
                error_code=E5004_TIMEOUT_ERROR,
            )

    async def build_pr_diff(self) -> PRDiff:
        self._ensure_open()
        self._ensure_budget()
        if self._built:
            raise PRDifferException(
                "GitLab PR diff session build_pr_diff may be called only once",
                error_code=E5009_CONFIGURATION_ERROR,
            )
        self._built = True

        inventory = admit_inventory(
            self._gitlab_snapshot,
            max_files_allowed=self._config.max_files_allowed,
        )
        contents = await self._content_fetcher.fetch_all(
            self._gitlab_snapshot,
            inventory,
            base_url=self._base_url,
            deadline_monotonic=self._deadline_monotonic,
        )
        return self._assembler.assemble(inventory, contents)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True


class GitLabSessionPRDiffReader:
    """Session-capable GitLab reader implementing open/build/close lifecycle."""

    def __init__(
        self,
        *,
        operations: GitLabOperations,
        runtime: GitLabRuntime,
        content_fetcher: GitLabContentFetcher,
        assembler: GitLabDiffAssembler,
        config: GitLabConfig,
        request_timeout_seconds: float | None = None,
    ) -> None:
        self._operations = operations
        self._runtime = runtime
        self._content_fetcher = content_fetcher
        self._assembler = assembler
        self._config = config
        self._request_timeout_seconds = float(request_timeout_seconds) if request_timeout_seconds is not None else float(config.pr_diff_request_timeout_seconds)

    async def open_pr_diff_session(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ) -> GitLabPRDiffSession:
        # Per-request deadline only (never stored on the process-shared runtime).
        deadline = time.monotonic() + self._request_timeout_seconds
        url = (base_url or GITLAB_COM_URL).rstrip("/")
        # Fail closed before any SDK work when host is not allowlisted.
        self._runtime.ensure_host_allowed(url)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DomainTimeoutError(
                "PR diff request deadline exhausted before session open",
                error_code=E5004_TIMEOUT_ERROR,
            )

        project_path = f"{repo_owner}/{repo_name}"
        operations = self._operations

        def select(client: gitlab.Gitlab) -> GitLabDiffSnapshot:
            return operations.select_with_client(client, project_path, pr_number)

        snapshot = await self._runtime.run_blocking(
            select,
            not_found=GitLabNotFoundContext(GitLabNotFoundKind.MERGE_REQUEST),
            base_url=url,
            deadline_monotonic=deadline,
        )
        return GitLabPRDiffSession(
            snapshot=snapshot,
            operations=self._operations,
            content_fetcher=self._content_fetcher,
            assembler=self._assembler,
            config=self._config,
            runtime=self._runtime,
            deadline_monotonic=deadline,
            base_url=url,
        )

    async def get_pr_diff(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ) -> PRDiff | None:
        session = await self.open_pr_diff_session(repo_owner, repo_name, pr_number, base_url=base_url)
        try:
            return await session.build_pr_diff()
        finally:
            await session.aclose()

    async def get_latest_commit_sha(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ) -> str | None:
        session = await self.open_pr_diff_session(repo_owner, repo_name, pr_number, base_url=base_url)
        try:
            return session.snapshot.head_sha
        finally:
            await session.aclose()
