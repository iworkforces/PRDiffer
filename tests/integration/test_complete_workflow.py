"""Integration tests for complete PR diff workflow.

These tests verify the end-to-end workflow from MCP server request
to GitHub API response, including all intermediate components.
"""

from unittest.mock import Mock, AsyncMock
import anyio
import pytest

from prdiffer.application.factory import create_mcp_server
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import StrictPRDiffCacheIdentity, github_full_diff_v3_identity
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffReadSessionInterface, PRDiffSnapshot
from prdiffer.infrastructure.github_repository import GitHubPRDiffRepository


class WorkflowPRDiffSession(PRDiffReadSessionInterface):
    def __init__(self, reader: "WorkflowPRDiffService", snapshot: PRDiffSnapshot) -> None:
        self._reader = reader
        self._snapshot = snapshot
        self._cache_identity = github_full_diff_v3_identity(
            snapshot.owner,
            snapshot.repo,
            snapshot.pr_number,
            self._snapshot.merge_base_sha,
            self._snapshot.head_sha,
        )

    @property
    def snapshot(self) -> PRDiffSnapshot:
        return self._snapshot

    @property
    def cache_identity(self) -> StrictPRDiffCacheIdentity:
        return self._cache_identity

    async def build_pr_diff(self) -> PRDiff:
        return await self._reader.build_pr_diff_mock()

    async def aclose(self) -> None:
        return None


class WorkflowPRDiffService:
    def __init__(self) -> None:
        self.build_pr_diff_mock = AsyncMock(return_value=PRDiff(files=()))

    async def open_pr_diff_session(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        /,
        *,
        base_url: str | None = None,
    ) -> PRDiffReadSessionInterface:
        del base_url
        snapshot = PRDiffSnapshot(repo_owner, repo_name, pr_number, "a" * 40, "b" * 40, "c" * 40, 0)
        return WorkflowPRDiffSession(self, snapshot)


@pytest.mark.integration
class TestCompleteWorkflow:
    """Integration tests for complete PR diff retrieval workflow."""

    @pytest.fixture
    def mock_github_repository(self):
        """Mock GitHub repository for testing."""
        mock_repo = Mock(spec=GitHubPRDiffRepository)
        return mock_repo

    @pytest.fixture
    def mock_settings(self):
        """Mock settings service."""
        mock_settings = Mock()
        mock_settings.get = Mock(
            side_effect=lambda key, default=None: {
                "app.debug": False,
                "app.log_level": "INFO",
                "app.max_files_allowed": 50,
                "github.rate_limit": 5000,
                "github.timeout": 30,
                "cache.ttl": 300,
                "cache.use_hashed_keys": True,
                "rate_limit.max_requests": 100,
                "rate_limit.window_seconds": 60,
                "mcp.transport": "stdio",
            }.get(key, default)
        )
        return mock_settings

    @pytest.fixture
    def mock_logger(self):
        """Mock logger service."""
        from prdiffer.infrastructure.logging.console_logger import ConsoleLogger

        logger = ConsoleLogger()
        # Disable actual logging during tests
        setattr(logger, "_logger", Mock())
        return logger

    @pytest.fixture
    def mock_cache(self):
        """Mock cache service."""
        mock_cache = Mock()
        mock_cache.get = Mock(return_value=None)
        mock_cache.set = Mock()
        mock_cache.invalidate = Mock()
        mock_cache.clear = Mock()
        mock_cache.get_stats = Mock(return_value={"size": 0})
        return mock_cache

    @pytest.fixture
    def mock_pr_diff_service(self):
        """Mock PR diff service."""
        return WorkflowPRDiffService()

    @pytest.fixture
    def sample_pr_diff(self):
        """Create sample PRDiff for testing."""
        return PRDiff(files=())

    def test_complete_workflow_success(
        self,
        mock_github_repository,
        mock_settings,
        mock_logger,
        mock_cache,
        mock_pr_diff_service,
        sample_pr_diff,
    ):
        """Test complete successful workflow from request to response."""
        # Arrange: Set up the PR diff response
        mock_pr_diff_service.build_pr_diff_mock.return_value = sample_pr_diff

        # Create server with mocked dependencies
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_github_repository,
            settings_service=mock_settings,
            cache_service=mock_cache,
            pr_diff_service=mock_pr_diff_service,
            logger=mock_logger,
        )

        # Verify server initialized properly
        assert server.mcp is not None

    def test_workflow_with_caching(
        self,
        mock_github_repository,
        mock_settings,
        mock_logger,
        mock_cache,
        mock_pr_diff_service,
        sample_pr_diff,
    ):
        """Test workflow with caching enabled."""
        # Arrange: Set up cached response
        mock_cache.get.return_value = sample_pr_diff
        mock_pr_diff_service.build_pr_diff_mock.return_value = sample_pr_diff

        # Create server
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_github_repository,
            settings_service=mock_settings,
            cache_service=mock_cache,
            pr_diff_service=mock_pr_diff_service,
            logger=mock_logger,
        )

        # Verify cache service is injected
        assert server._cache_service == mock_cache

    def test_workflow_with_metrics_tracking(
        self,
        mock_github_repository,
        mock_settings,
        mock_logger,
        mock_cache,
        mock_pr_diff_service,
    ):
        """Test workflow with metrics tracking."""
        # Arrange
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_github_repository,
            settings_service=mock_settings,
            cache_service=mock_cache,
            pr_diff_service=mock_pr_diff_service,
            logger=mock_logger,
        )

        # Verify metrics tracker is available
        assert server._metrics_tracker is not None
        assert hasattr(server._metrics_tracker, "track_request")
        assert hasattr(server._metrics_tracker, "get_metrics_summary")

    def test_workflow_with_health_monitoring(
        self,
        mock_github_repository,
        mock_settings,
        mock_logger,
        mock_cache,
        mock_pr_diff_service,
    ):
        """Test workflow with health monitoring."""
        # Arrange
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_github_repository,
            settings_service=mock_settings,
            cache_service=mock_cache,
            pr_diff_service=mock_pr_diff_service,
            logger=mock_logger,
        )

        # Verify health monitor is available
        assert server._health_monitor is not None
        assert hasattr(server._health_monitor, "check_health")

        # Get health status
        health = anyio.run(server._health_endpoints._get_health_status)
        assert "status" in health
        assert "uptime_seconds" in health

    def test_workflow_with_rate_limiting(
        self,
        mock_github_repository,
        mock_settings,
        mock_logger,
        mock_cache,
        mock_pr_diff_service,
    ):
        """Test workflow with rate limiting."""
        # Arrange
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_github_repository,
            settings_service=mock_settings,
            cache_service=mock_cache,
            pr_diff_service=mock_pr_diff_service,
            logger=mock_logger,
        )

        # Verify rate limiter is available
        assert server._rate_limiter is not None
        assert hasattr(server._rate_limiter, "check_rate_limit")
        assert hasattr(server._rate_limiter, "increment_rate_limit")

    def test_workflow_with_authentication(
        self,
        mock_github_repository,
        mock_settings,
        mock_logger,
        mock_cache,
        mock_pr_diff_service,
    ):
        """Test workflow with authentication."""
        # Arrange
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_github_repository,
            settings_service=mock_settings,
            cache_service=mock_cache,
            pr_diff_service=mock_pr_diff_service,
            logger=mock_logger,
        )

        # Verify authentication middleware is available
        assert server._authentication is not None
        assert hasattr(server._authentication, "authenticate")

        # Test authentication (disabled by default)
        is_auth, client_id = server._authentication.authenticate(None)
        # When disabled, should allow all requests
        assert is_auth is True
        assert client_id == "anonymous"

    def test_workflow_component_integration(
        self,
        mock_github_repository,
        mock_settings,
        mock_logger,
        mock_cache,
        mock_pr_diff_service,
    ):
        """Test that all components are properly integrated."""
        # Arrange
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_github_repository,
            settings_service=mock_settings,
            cache_service=mock_cache,
            pr_diff_service=mock_pr_diff_service,
            logger=mock_logger,
        )

        # Verify all components are injected
        assert server._settings_service == mock_settings
        assert server._cache_service == mock_cache
        assert server._logger == mock_logger
        assert server._rate_limiter is not None
        assert server._metrics_tracker is not None
        assert server._health_monitor is not None
        assert server._server_configuration is not None
        assert server._authentication is not None
        assert server._input_validator is not None
        assert server._request_coalescing is not None

    def test_workflow_request_coalescing(
        self,
        mock_github_repository,
        mock_settings,
        mock_logger,
        mock_cache,
        mock_pr_diff_service,
    ):
        """Test workflow with request coalescing."""
        # Arrange
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_github_repository,
            settings_service=mock_settings,
            cache_service=mock_cache,
            pr_diff_service=mock_pr_diff_service,
            logger=mock_logger,
        )

        # Verify request coalescing service is available
        assert server._request_coalescing is not None
        assert hasattr(server._request_coalescing, "coalesce")


@pytest.mark.integration
class TestWorkflowWithRealServices:
    """Integration tests with real (non-mocked) infrastructure services."""

    @pytest.fixture
    def real_settings(self):
        """Create real settings service with test configuration."""
        from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory

        factory = get_infrastructure_factory()
        return factory.create_settings_service()

    @pytest.fixture
    def real_logger(self):
        """Create real logger service."""
        from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory

        factory = get_infrastructure_factory()
        return factory.create_logger_service()

    @pytest.fixture
    def real_cache(self):
        """Create real cache service."""
        from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory

        factory = get_infrastructure_factory()
        return factory.create_cache_service()

    @pytest.fixture
    def mock_repository(self):
        """Mock repository for testing."""
        mock_repo = Mock(spec=GitHubPRDiffRepository)
        return mock_repo

    def test_real_services_integration(self, real_settings, real_logger, real_cache, mock_repository):
        """Test integration with real infrastructure services."""
        # This test uses real services (except GitHub repository)
        # to verify service compatibility

        # Create PR diff service fake
        mock_pr_diff_service = WorkflowPRDiffService()

        # Create server with real services
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_repository,
            settings_service=real_settings,
            cache_service=real_cache,
            pr_diff_service=mock_pr_diff_service,
            logger=real_logger,
        )

        # Verify server initialized with real services
        assert server.mcp is not None
        assert server._settings_service == real_settings
        assert server._cache_service == real_cache
        assert server._logger == real_logger

    async def test_cache_operations(self, real_cache):
        """Test real cache service operations."""
        # Create sample PR diff with new structure
        from prdiffer.domain.entities.file_diff_response import (
            FileDiffResponse,
            FileStats,
        )
        from prdiffer.domain.entities.file_patch import EDIT_TYPE

        pr_diff = PRDiff(
            files=(
                FileDiffResponse(
                    path="test.py",
                    status=EDIT_TYPE.MODIFIED,
                    stats=FileStats(additions=5, deletions=2),
                    diff="test diff content",
                ),
            )
        )

        # Test cache set and get
        await real_cache.set("owner/repo/pr/123", "abc123", pr_diff)

        # Verify cache contains data
        stats = real_cache.get_stats()
        assert stats["cache_size"] == 1

        # Test cache retrieval
        result = await real_cache.get("owner/repo/pr/123", "abc123")
        assert result is not None
        assert result.files[0].diff == "test diff content"

        # Clean up
        await real_cache.clear()


@pytest.mark.integration
class TestEndToEndScenarios:
    """End-to-end scenario tests."""

    def test_server_initialization_scenario(self):
        """Test complete server initialization scenario."""
        from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory

        factory = get_infrastructure_factory()

        # Mock repository for testing
        mock_repo = Mock(spec=GitHubPRDiffRepository)

        # Fake PR diff service
        mock_pr_diff_service = WorkflowPRDiffService()

        # Create all services through factory
        settings_service = factory.create_settings_service()
        logger_service = factory.create_logger_service()
        cache_service = factory.create_cache_service()

        # Create server
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_repo,
            settings_service=settings_service,
            cache_service=cache_service,
            pr_diff_service=mock_pr_diff_service,
            logger=logger_service,
        )

        # Verify complete initialization
        assert server.mcp is not None
        assert server._input_validator is not None
        assert server._request_coalescing is not None

    def test_component_interaction_scenario(self):
        """Test interaction between components."""
        from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory
        from prdiffer.application.factories.application_factory import get_application_factory

        infra_factory = get_infrastructure_factory()
        app_factory = get_application_factory()

        # Mock repository
        mock_repo = Mock(spec=GitHubPRDiffRepository)

        # Fake PR diff service
        mock_pr_diff_service = WorkflowPRDiffService()

        # Create services
        settings_service = infra_factory.create_settings_service()
        logger_service = infra_factory.create_logger_service()
        cache_service = infra_factory.create_cache_service()
        metrics_tracker = app_factory.create_metrics_tracker(logger_service)
        rate_limiter = app_factory.create_rate_limiter(logger_service)

        # Create server (not directly used, but validates wiring)
        _ = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_repo,
            settings_service=settings_service,
            cache_service=cache_service,
            pr_diff_service=mock_pr_diff_service,
            logger=logger_service,
        )

        # Test rate limiter integration
        is_allowed = rate_limiter.check_rate_limit("test_client")
        assert is_allowed is True

        rate_limiter.increment_rate_limit("test_client")

        # Test metrics tracker integration
        request_id = metrics_tracker.generate_request_id()
        assert request_id.startswith("REQ-")

        metrics_tracker.track_request("test_operation", True, 0.5)

        summary = metrics_tracker.get_metrics_summary()
        assert summary["total_requests"] == 1

    def test_health_check_scenario(self):
        """Test health check scenario."""
        from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory

        factory = get_infrastructure_factory()

        # Mock repository
        mock_repo = Mock(spec=GitHubPRDiffRepository)

        # Fake PR diff service
        mock_pr_diff_service = WorkflowPRDiffService()

        # Create services
        settings_service = factory.create_settings_service()
        logger_service = factory.create_logger_service()
        cache_service = factory.create_cache_service()

        # Create server
        server = create_mcp_server(
            github_repository_class=lambda o, r, n: mock_repo,
            settings_service=settings_service,
            cache_service=cache_service,
            pr_diff_service=mock_pr_diff_service,
            logger=logger_service,
        )

        # Get health status
        health = anyio.run(server._health_endpoints._get_health_status)

        # Verify health status structure
        assert "status" in health
        assert "uptime_seconds" in health
        assert "total_requests" in health
        assert "successful_requests" in health
        assert "failed_requests" in health
