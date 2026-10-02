"""Async GitLab adapter that maps SDK responses into domain diff entities."""

import re

import anyio.to_thread

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.error_codes import E5002_GITHUB_API_ERROR
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.domain.interfaces.vcs_provider import VCSDiffRepositoryInterface
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.utils.diff_utils import DiffUtils
from prdiffer.infrastructure.vcs_providers.gitlab_content import GitLabContentFetcher
from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import GitLabDiffAssembler
from prdiffer.infrastructure.vcs_providers.gitlab_diff_session import GitLabSessionPRDiffReader
from prdiffer.infrastructure.vcs_providers.gitlab_models import GitLabDiffRecord
from prdiffer.infrastructure.vcs_providers.gitlab_operations import GitLabOperations
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import GitLabRuntime


class GitLabVCSRepository(VCSDiffRepositoryInterface):
    """Provide the asynchronous VCS contract over the strict GitLab session path."""

    def __init__(
        self,
        gitlab_token: str | None = None,
        *,
        config: GitLabConfig | None = None,
        runtime: GitLabRuntime | None = None,
        operations: GitLabOperations | None = None,
        session_reader: GitLabSessionPRDiffReader | None = None,
    ) -> None:
        self._config = config or GitLabConfig()
        self._operations = operations or GitLabOperations(
            gitlab_token,
            allowed_hosts=tuple(self._config.allowed_hosts),
        )
        self._runtime = runtime or GitLabRuntime(self._config, private_token=gitlab_token)
        if session_reader is not None:
            self._session_reader = session_reader
        else:
            content = GitLabContentFetcher(self._runtime, self._config)
            assembler = GitLabDiffAssembler(
                DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False),
                self._config,
            )
            self._session_reader = GitLabSessionPRDiffReader(
                operations=self._operations,
                runtime=self._runtime,
                content_fetcher=content,
                assembler=assembler,
                config=self._config,
            )

    @property
    def provider_name(self) -> str:
        """Get provider name."""
        return "gitlab"

    @property
    def provider_version(self) -> str:
        """Get provider API version."""
        return "v4"

    async def initialize(self) -> None:
        """Validate the configured GitLab connection without blocking the event loop."""
        await anyio.to_thread.run_sync(self._operations.initialize, abandon_on_cancel=False)

    async def open_pr_diff_session(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ):
        """Open a request-scoped strict full-diff session."""
        return await self._session_reader.open_pr_diff_session(repo_owner, repo_name, pr_number, base_url=base_url)

    async def get_pr_diff(self, owner: str, repo: str, pr: int) -> PRDiff:
        """Build a complete PRDiff via open/build/close session lifecycle."""
        result = await self._session_reader.get_pr_diff(owner, repo, pr)
        if result is None:
            raise PRDifferException("GitLab PR diff returned no data", error_code=E5002_GITHUB_API_ERROR)
        return result

    async def get_latest_commit_sha(self, owner: str, repo: str, pr: int) -> str:
        """Return head SHA from an independently opened/closed session."""
        sha = await self._session_reader.get_latest_commit_sha(owner, repo, pr)
        if not sha:
            raise PRDifferException("Merge request SHA is missing", error_code=E5002_GITHUB_API_ERROR)
        return sha

    async def approve_pr_with_comment(
        self,
        owner: str,
        repo: str,
        pr: int,
        compliment: str,
        /,
        *,
        base_url: str | None = None,
    ) -> str:
        """Approve a GitLab MR and post the compliment as a note (off event loop)."""
        project_path = f"{owner}/{repo}"
        return await self._runtime.run_blocking(
            lambda client: self._operations.approve_with_client(client, project_path, pr, compliment),
            not_found=None,
            base_url=base_url,
        )

    async def update_pr_description(
        self,
        owner: str,
        repo: str,
        pr: int,
        description: str,
        /,
        *,
        base_url: str | None = None,
    ) -> str:
        """Update a GitLab MR description field (off event loop)."""
        project_path = f"{owner}/{repo}"
        return await self._runtime.run_blocking(
            lambda client: self._operations.update_description_with_client(client, project_path, pr, description),
            not_found=None,
            base_url=base_url,
        )

    @staticmethod
    def _to_file_diff(record: GitLabDiffRecord) -> FileDiffResponse:
        """Map a GitLab diff record to the existing domain response."""
        patch: str = record.diff if record.diff is not None else ""
        match (record.new_file, record.deleted_file, record.renamed_file):
            case (True, False, False):
                path, status = record.new_path, EDIT_TYPE.ADDED
            case (False, True, False):
                path, status = record.old_path, EDIT_TYPE.DELETED
            case (False, False, True):
                path, status = record.new_path, EDIT_TYPE.RENAMED
            case (False, False, False):
                path, status = record.new_path, EDIT_TYPE.MODIFIED
            case _:
                raise PRDifferException("GitLab diff record has conflicting change flags", error_code=E5002_GITHUB_API_ERROR)

        previous_path: str | None = None
        if status is EDIT_TYPE.RENAMED:
            # Mechanical mapping: use GitLab old_path when present and distinct.
            if record.old_path and record.old_path != path:
                previous_path = record.old_path

        return FileDiffResponse(
            path=path,
            status=status,
            stats=FileStats(
                additions=sum(line.startswith("+") and not line.startswith("+++") for line in patch.splitlines()),
                deletions=sum(line.startswith("-") and not line.startswith("---") for line in patch.splitlines()),
            ),
            diff=patch,
            previous_path=previous_path,
        )

    def supports_repository(self, url: str) -> bool:
        """Check whether the URL belongs to a supported canonical GitLab resource.

        Merge request URLs accept nested namespaces (group/subgroup/project).
        """
        if not url:
            return False
        from prdiffer.infrastructure.utils.url_parser import parse_gitlab_merge_request_url
        from prdiffer.domain.exceptions import InvalidPRNumberError, InvalidURLError

        try:
            parse_gitlab_merge_request_url(url)
            return True
        except InvalidURLError, InvalidPRNumberError:
            pass

        # Legacy simple/nested tree URLs on GitLab.com (read-only support check).
        tree_pattern = re.compile(
            r"^https://gitlab\.com/(?:[a-zA-Z0-9](?:[a-zA-Z0-9._-]*[a-zA-Z0-9])?/)+"
            r"-/tree/[a-zA-Z0-9._/-]+/?$"
        )
        return bool(tree_pattern.match(url.strip()))
