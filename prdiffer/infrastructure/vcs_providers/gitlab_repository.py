"""Async GitLab adapter over the strict session diff path and MR write operations."""

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.error_codes import E5002_GITHUB_API_ERROR
from prdiffer.domain.exceptions import PRDifferException
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.utils.diff_utils import DiffUtils
from prdiffer.infrastructure.vcs_providers.gitlab_content import GitLabContentFetcher
from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import GitLabDiffAssembler
from prdiffer.infrastructure.vcs_providers.gitlab_diff_session import GitLabSessionPRDiffReader
from prdiffer.infrastructure.vcs_providers.gitlab_operations import GitLabOperations
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import GitLabRuntime


class GitLabVCSRepository:
    """Expose the strict GitLab session reader and MR approve/describe operations."""

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
        self._operations = operations or GitLabOperations()
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
