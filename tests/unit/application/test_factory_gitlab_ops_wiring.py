"""Composition tests: create_mcp_server auto-wires GitLab MR ops from the reader."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prdiffer.application.factory import _is_gitlab_pr_operations, create_mcp_server
from prdiffer.application.provider_resolver import ProviderTarget
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import StrictPRDiffCacheIdentity
from prdiffer.domain.exceptions import ProviderCapabilityUnavailableError
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffReadSessionInterface, PRDiffSnapshot
from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.infrastructure.vcs_providers.gitlab_operations import GitLabOperations
from prdiffer.infrastructure.vcs_providers.gitlab_repository import GitLabVCSRepository
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import GitLabRuntime


class StrictSession(PRDiffReadSessionInterface):
    @property
    def snapshot(self) -> PRDiffSnapshot:
        return PRDiffSnapshot("group", "project", 3, "a" * 40, "b" * 40, "c" * 40, 0)

    @property
    def cache_identity(self) -> StrictPRDiffCacheIdentity:
        return StrictPRDiffCacheIdentity("test:group:project:3", "token", 1)

    async def build_pr_diff(self) -> PRDiff:
        return PRDiff(files=())

    async def aclose(self) -> None:
        return None


class ReaderOnly:
    """Diff reader without approve/describe methods."""

    async def open_pr_diff_session(
        self, owner: str, repo: str, pr: int, /, *, base_url: str | None = None
    ) -> PRDiffReadSessionInterface:
        return StrictSession()


class DualRoleReader:
    """Structural dual: session reader methods + MR ops signatures."""

    def __init__(self) -> None:
        self.approval_requests: list[tuple[str, str, int, str, str | None]] = []

    async def open_pr_diff_session(
        self, owner: str, repo: str, pr: int, /, *, base_url: str | None = None
    ) -> PRDiffReadSessionInterface:
        return StrictSession()

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
        self.approval_requests.append((owner, repo, pr, compliment, base_url))
        return f"approved:{owner}/{repo}!{pr}"

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
        return f"described:{owner}/{repo}!{pr}"


@pytest.mark.unit
class TestIsGitLabPROperations:
    def test_dual_role_reader_matches(self) -> None:
        assert _is_gitlab_pr_operations(DualRoleReader()) is True

    def test_reader_only_does_not_match(self) -> None:
        assert _is_gitlab_pr_operations(ReaderOnly()) is False

    def test_real_gitlab_vcs_repository_matches(self) -> None:
        config = GitLabConfig()
        runtime = GitLabRuntime(config, private_token="t")
        repo = GitLabVCSRepository(
            "t",
            config=config,
            runtime=runtime,
            operations=GitLabOperations(),
            session_reader=MagicMock(),
        )
        assert _is_gitlab_pr_operations(repo) is True


@pytest.mark.unit
class TestCreateMcpServerGitLabOpsWiring:
    def _stub_deps(self):
        """Patch infrastructure/application factories used by create_mcp_server."""
        infra = MagicMock()
        infra.create_settings_service.return_value = MagicMock()
        infra.create_logger_service.return_value = MagicMock()
        infra.create_cache_service.return_value = MagicMock()
        infra.create_repository_cache_service.return_value = MagicMock()
        infra.create_pr_diff_service.return_value = ReaderOnly()
        infra.create_input_validator.return_value = MagicMock()
        infra.create_diff_service.return_value = MagicMock()
        infra.create_pattern_matching_service.return_value = MagicMock()

        app = MagicMock()
        app.create_rate_limiter.return_value = MagicMock()
        app.create_metrics_tracker.return_value = MagicMock()
        app.create_server_configuration.return_value = MagicMock()
        app.create_authentication.return_value = MagicMock()
        app.create_health_monitor.return_value = MagicMock()
        return infra, app

    @pytest.mark.anyio
    async def test_auto_wires_gitlab_pr_operations_from_dual_reader(self) -> None:
        dual = DualRoleReader()
        infra, app = self._stub_deps()
        with (
            patch("prdiffer.application.factory.get_infrastructure_factory", return_value=infra),
            patch("prdiffer.application.factory.get_application_factory", return_value=app),
            patch(
                "prdiffer.infrastructure.utils.coalescing_service.get_request_coalescing_service",
                return_value=MagicMock(),
            ),
            patch("prdiffer.application.mcp_server.FastMCP"),
        ):
            server = create_mcp_server(
                github_repository_class=MagicMock,
                gitlab_reader=dual,
                gitlab_pr_operations=None,
            )

        target = ProviderTarget("gitlab", "group", "project", 3, "https://gitlab.com/group/project/-/merge_requests/3", "https://gitlab.com")
        assert server._provider_resolver.resolve_strict_diff(target).reader is dual
        assert await server._provider_resolver.resolve_approval(target).approve(target, "Nice work") == "approved:group/project!3"
        assert dual.approval_requests == [("group", "project", 3, "Nice work", "https://gitlab.com")]

    @pytest.mark.anyio
    async def test_explicit_ops_preferred_over_reader(self) -> None:
        dual = DualRoleReader()
        explicit = DualRoleReader()
        infra, app = self._stub_deps()
        with (
            patch("prdiffer.application.factory.get_infrastructure_factory", return_value=infra),
            patch("prdiffer.application.factory.get_application_factory", return_value=app),
            patch(
                "prdiffer.infrastructure.utils.coalescing_service.get_request_coalescing_service",
                return_value=MagicMock(),
            ),
            patch("prdiffer.application.mcp_server.FastMCP"),
        ):
            server = create_mcp_server(
                github_repository_class=MagicMock,
                gitlab_reader=dual,
                gitlab_pr_operations=explicit,
            )

        target = ProviderTarget("gitlab", "group", "project", 3, "https://gitlab.com/group/project/-/merge_requests/3", "https://gitlab.com")
        assert await server._provider_resolver.resolve_approval(target).approve(target, "Nice work") == "approved:group/project!3"
        assert dual.approval_requests == []
        assert explicit.approval_requests == [("group", "project", 3, "Nice work", "https://gitlab.com")]

    def test_reader_without_ops_leaves_ops_none(self) -> None:
        reader = ReaderOnly()
        infra, app = self._stub_deps()
        with (
            patch("prdiffer.application.factory.get_infrastructure_factory", return_value=infra),
            patch("prdiffer.application.factory.get_application_factory", return_value=app),
            patch(
                "prdiffer.infrastructure.utils.coalescing_service.get_request_coalescing_service",
                return_value=MagicMock(),
            ),
            patch("prdiffer.application.mcp_server.FastMCP"),
        ):
            server = create_mcp_server(
                github_repository_class=MagicMock,
                gitlab_reader=reader,
                gitlab_pr_operations=None,
            )

        target = ProviderTarget("gitlab", "group", "project", 3, "https://gitlab.com/group/project/-/merge_requests/3", "https://gitlab.com")
        assert server._provider_resolver.resolve_strict_diff(target).reader is reader
        with pytest.raises(ProviderCapabilityUnavailableError):
            server._provider_resolver.resolve_approval(target)
