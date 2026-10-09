"""Unit tests for GitHubPRDiffRepository.

Tests covering initialization, GitHub object setup, PR approval,
description updates, and error handling.
"""

import pytest
from threading import get_ident
from unittest.mock import Mock, PropertyMock, patch
from github.GithubException import (
    GithubException,
    UnknownObjectException,
    RateLimitExceededException,
)

from prdiffer.domain.config.github_config import GitHubConfig
from prdiffer.infrastructure.github_repository import GitHubPRDiffRepository
from prdiffer.domain.exceptions import HeadSHAMismatchError, PRDifferException
from prdiffer.domain.error_codes import E1011_HEAD_SHA_MISMATCH


@pytest.fixture
def mock_settings():
    """Create mock settings service backed by a default GitHubConfig."""
    settings = Mock()
    settings.get_github_config.return_value = GitHubConfig()
    return settings


@pytest.fixture
def mock_logger():
    """Create mock logger."""
    logger = Mock()
    logger.should_log.return_value = False
    return logger


@pytest.fixture
def mock_input_validator():
    """Create mock input validator."""
    validator = Mock()
    validator.sanitize_for_logging.side_effect = lambda s, max_length=None: s[:max_length] if max_length else s
    validator.validate_github_url.return_value = ("owner", "repo", 123)
    return validator


@pytest.fixture
def mock_github_api_client():
    """Create mock GitHub API client."""
    client = Mock()
    client.initialize_client = Mock()
    client._get_pygithub_repository = Mock()
    client._get_pygithub_pull_request = Mock()
    return client


@pytest.fixture
def repository(
    mock_settings,
    mock_logger,
    mock_input_validator,
    mock_github_api_client,
):
    """Create GitHubPRDiffRepository instance with mocked dependencies."""
    with patch(
        "prdiffer.infrastructure.github_repository.get_github_api_client",
        return_value=mock_github_api_client,
    ):
        repo = GitHubPRDiffRepository(
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            github_token="test-token",
            settings_service=mock_settings,
            logger=mock_logger,
            input_validator=mock_input_validator,
        )
        # Attach mocks for test access
        repo._mock_api_client = mock_github_api_client
        return repo


class TestGitHubPRDiffRepositoryInit:
    """Tests for repository initialization."""

    def test_init_with_all_parameters(self, mock_settings, mock_logger, mock_input_validator):
        """Test initialization with all parameters."""
        with patch("prdiffer.infrastructure.github_repository.get_github_api_client") as mock_get_client:
            mock_get_client.return_value = Mock()

            repo = GitHubPRDiffRepository(
                repo_owner="owner",
                repo_name="repo",
                pr_number=456,
                github_token="token123",
                settings_service=mock_settings,
                logger=mock_logger,
                input_validator=mock_input_validator,
            )

            assert repo.repo_owner == "owner"
            assert repo.repo_name == "repo"
            assert repo.pr_number == 456
            assert repo.github_token == "token123"

    def test_init_uses_env_token_if_not_provided(self, mock_settings, mock_logger, mock_input_validator, monkeypatch):
        """Test that GITHUB_TOKEN env var is used if token not provided."""
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")

        with patch("prdiffer.infrastructure.github_repository.get_github_api_client") as mock_get_client:
            mock_get_client.return_value = Mock()

            repo = GitHubPRDiffRepository(
                repo_owner="owner",
                repo_name="repo",
                pr_number=123,
                settings_service=mock_settings,
                logger=mock_logger,
                input_validator=mock_input_validator,
            )

            assert repo.github_token == "env-token"

    def test_client_built_from_github_config(self, mock_logger, mock_input_validator):
        """GitHubConfig values (including disabled retry features) reach the client's retry handler."""
        settings = Mock()
        settings.get_github_config.return_value = GitHubConfig(
            timeout=45,
            max_retries=5,
            retry_on_403=False,
            circuit_breaker_enabled=False,
            api_health_tracking=False,
        )

        repo = GitHubPRDiffRepository(
            repo_owner="owner",
            repo_name="repo",
            pr_number=1,
            github_token="token",
            settings_service=settings,
            logger=mock_logger,
            input_validator=mock_input_validator,
        )

        handler = repo._github_api_client._retry_handler
        assert repo.timeout == 45
        assert handler.max_retries == 5
        assert handler.retry_on_403 is False
        assert handler.circuit_breaker_enabled is False
        assert handler._circuit_breaker is None
        assert handler._health_tracker is None


class TestGitHubPRDiffRepositoryProperties:
    """Tests for repository properties."""

    def test_repo_owner_property(self, repository):
        """Test repo_owner property returns correct value."""
        assert repository.repo_owner == "owner"

    def test_repo_name_property(self, repository):
        """Test repo_name property returns correct value."""
        assert repository.repo_name == "repo"

    def test_pr_number_property(self, repository):
        """Test pr_number property returns correct value."""
        assert repository.pr_number == 123


class TestGitHubPRDiffRepositoryInitializeGitHubObjects:
    """Tests for the _initialize_github_objects method."""

    @pytest.mark.asyncio
    async def test_initialize_success(self, repository):
        """Test successful initialization."""
        mock_repo = Mock()
        mock_pr = Mock()
        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        await repository._initialize_github_objects()

        assert repository._initialized is True

    @pytest.mark.asyncio
    async def test_initialize_repository_not_found(self, repository):
        """Test initialization fails when repository not found."""
        repository._mock_api_client._get_pygithub_repository.side_effect = UnknownObjectException(404, "Not found")

        with pytest.raises(PRDifferException) as exc_info:
            await repository._initialize_github_objects()

        assert "Failed to initialize repository" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_initialize_rate_limit_exceeded(self, repository):
        """Test initialization fails when rate limit exceeded."""
        repository._mock_api_client._get_pygithub_repository.side_effect = RateLimitExceededException(403, "Rate limit")

        with pytest.raises(PRDifferException) as exc_info:
            await repository._initialize_github_objects()

        assert "Failed to initialize repository" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_initialize_github_exception(self, repository):
        """Test initialization fails on generic GitHub exception."""
        repository._mock_api_client._get_pygithub_repository.side_effect = GithubException(500, "Server error")

        with pytest.raises(PRDifferException) as exc_info:
            await repository._initialize_github_objects()

        assert "GitHub API error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_initialize_pr_not_found(self, repository):
        """Test initialization fails when PR not found."""
        mock_repo = Mock()
        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.side_effect = UnknownObjectException(404, "PR not found")

        with pytest.raises(PRDifferException) as exc_info:
            await repository._initialize_github_objects()

        assert "Failed to initialize pull request" in str(exc_info.value)


class TestGitHubPRDiffRepositoryApprovePR:
    """Tests for approve_pr_with_comment method."""

    @pytest.mark.asyncio
    async def test_approve_pr_success(self, repository):
        """Test approving PR successfully."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_review = Mock()
        mock_review.id = "review123"
        mock_pr.create_review.return_value = mock_review

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        result = await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great work!")

        assert "Successfully approved PR" in result
        mock_pr.create_review.assert_called_once_with(event="APPROVE", body="Great work!")

    @pytest.mark.asyncio
    async def test_approve_pr_empty_compliment(self, repository):
        """Test approving PR with empty compliment fails."""
        with pytest.raises(ValueError, match="Compliment cannot be empty"):
            await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "")

    @pytest.mark.asyncio
    async def test_approve_pr_non_string_compliment(self, repository):
        """Test approving PR with non-string compliment fails."""
        with pytest.raises(ValueError, match="Compliment must be a string"):
            await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", 123)

    @pytest.mark.asyncio
    async def test_approve_pr_404_error(self, repository):
        """Test approving PR handles 404 error."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.create_review.side_effect = GithubException(404, "Not found")

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        with pytest.raises(RuntimeError, match="not found"):
            await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great!")

    @pytest.mark.asyncio
    async def test_approve_pr_403_error(self, repository):
        """Test approving PR handles 403 forbidden error."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.create_review.side_effect = GithubException(403, "Forbidden")

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        with pytest.raises(RuntimeError, match="Insufficient permissions"):
            await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great!")

    @pytest.mark.asyncio
    async def test_approve_pr_rate_limit_error(self, repository):
        """Test approving PR handles rate limit error."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.create_review.side_effect = GithubException(429, "Rate limit exceeded")

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        with pytest.raises(RuntimeError, match="rate limit exceeded"):
            await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great!")

    @pytest.mark.asyncio
    async def test_approve_pr_generic_error(self, repository):
        """Test approving PR handles generic GitHub error."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.create_review.side_effect = GithubException(500, "Server error")

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        with pytest.raises(RuntimeError, match="GitHub API error"):
            await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great!")


class TestGitHubPRDiffRepositoryHeadBoundApproval:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("head_sha", ["a" * 40, "A" * 40, "a" * 64])
    async def test_approve_pr_matching_head_binds_exact_commit(self, repository: GitHubPRDiffRepository, mock_github_api_client: Mock, head_sha: str):
        # Given
        expected = head_sha.casefold()
        cached_pr = Mock()
        fresh_pr = Mock()
        fresh_pr.head.sha = head_sha
        commit = Mock()
        provider_repo = Mock()
        provider_repo.get_pull.return_value = fresh_pr
        provider_repo.get_commit.return_value = commit
        mock_github_api_client._get_pygithub_repository.return_value = provider_repo
        mock_github_api_client._get_pygithub_pull_request.return_value = cached_pr

        # When
        result = await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great work!", expected_head_sha=expected)

        # Then
        assert result == "Successfully approved PR #123 in owner/repo"
        provider_repo.get_pull.assert_called_once_with(123)
        provider_repo.get_commit.assert_called_once_with(expected)
        fresh_pr.create_review.assert_called_once_with(commit=commit, event="APPROVE", body="Great work!")
        assert fresh_pr.create_review.call_args.kwargs["commit"] is commit
        cached_pr.create_review.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cached_head", ["a" * 40, "c" * 40])
    async def test_approve_pr_moved_fresh_head_rejects_even_matching_cached_head(self, repository: GitHubPRDiffRepository, cached_head: str):
        # Given: the instance was initialized earlier, potentially at the expected head.
        expected = "a" * 40
        cached_pr = Mock()
        cached_pr.head.sha = cached_head
        fresh_pr = Mock()
        fresh_pr.head.sha = "B" * 40
        provider_repo = Mock()
        provider_repo.get_pull.return_value = fresh_pr
        repository._repository = provider_repo
        repository._pull_request = cached_pr
        repository._initialized = True

        # When
        with pytest.raises(HeadSHAMismatchError) as exc_info:
            await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great work!", expected_head_sha=expected)

        # Then
        assert exc_info.value.error_code == E1011_HEAD_SHA_MISMATCH
        assert exc_info.value.details == {"expected_head_sha": expected, "actual_head_sha": "b" * 40}
        provider_repo.get_pull.assert_called_once_with(123)
        provider_repo.get_commit.assert_not_called()
        fresh_pr.create_review.assert_not_called()
        cached_pr.create_review.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stage", ["get_pull", "get_commit", "create_review"])
    @pytest.mark.parametrize(
        ("status", "message"),
        [(404, "not found"), (403, "Insufficient permissions"), (429, "rate limit exceeded"), (500, "GitHub API error")],
    )
    async def test_approve_pr_head_bound_github_exception_keeps_mapping(self, repository: GitHubPRDiffRepository, stage: str, status: int, message: str):
        # Given
        fresh_pr = Mock()
        fresh_pr.head.sha = "a" * 40
        provider_repo = Mock()
        provider_repo.get_pull.return_value = fresh_pr
        repository._repository = provider_repo
        repository._pull_request = Mock()
        repository._initialized = True
        error = GithubException(status, "Provider failure")
        target = fresh_pr if stage == "create_review" else provider_repo
        getattr(target, stage).side_effect = error

        # When
        with pytest.raises(RuntimeError, match=message) as exc_info:
            await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great work!", expected_head_sha="a" * 40)

        # Then
        assert exc_info.value.__cause__ is error

    @pytest.mark.asyncio
    async def test_approve_pr_without_sha_keeps_unbound_call(self, repository: GitHubPRDiffRepository):
        # Given
        cached_pr = Mock()
        provider_repo = Mock()
        repository._repository = provider_repo
        repository._pull_request = cached_pr
        repository._initialized = True

        # When
        result = await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great work!")

        # Then
        assert result == "Successfully approved PR #123 in owner/repo"
        cached_pr.create_review.assert_called_once_with(event="APPROVE", body="Great work!")
        provider_repo.get_commit.assert_not_called()
        provider_repo.get_pull.assert_not_called()

    @pytest.mark.asyncio
    async def test_approve_pr_head_bound_work_runs_in_one_worker_thread(self, repository: GitHubPRDiffRepository):
        # Given
        event_loop_thread = get_ident()
        work: list[tuple[str, int]] = []
        fresh_pr = Mock()
        head = Mock(sha="a" * 40)
        commit = Mock()
        review = Mock()
        provider_repo = Mock()
        repository._repository = provider_repo
        repository._pull_request = Mock()
        repository._initialized = True

        def get_pull(number: int) -> Mock:
            work.append(("get_pull", get_ident()))
            assert number == 123
            return fresh_pr

        def get_head() -> Mock:
            work.append(("head", get_ident()))
            return head

        def get_commit(sha: str) -> Mock:
            work.append(("get_commit", get_ident()))
            assert sha == "a" * 40
            return commit

        def create_review(*, commit: Mock, event: str, body: str) -> Mock:
            work.append(("create_review", get_ident()))
            return review

        provider_repo.get_pull.side_effect = get_pull
        provider_repo.get_commit.side_effect = get_commit
        fresh_pr.create_review.side_effect = create_review

        # When: use real asyncer rather than replacing its thread offload.
        with patch.object(type(fresh_pr), "head", new=PropertyMock(side_effect=get_head), create=True):
            await repository.approve_pr_with_comment("https://github.com/owner/repo/pull/123", "Great work!", expected_head_sha="a" * 40)

        # Then
        assert [stage for stage, _ in work] == ["get_pull", "head", "get_commit", "create_review"]
        worker_threads = {thread for _, thread in work}
        assert len(worker_threads) == 1
        assert event_loop_thread not in worker_threads


class TestGitHubPRDiffRepositoryUpdatePRDescription:
    """Tests for update_pr_description method."""

    @pytest.mark.asyncio
    async def test_update_pr_description_success(self, repository):
        """Test updating PR description successfully."""
        mock_repo = Mock()
        mock_pr = Mock()

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        result = await repository.update_pr_description("https://github.com/owner/repo/pull/123", "New description text")

        assert "Successfully updated description" in result
        mock_pr.edit.assert_called_once_with(body="New description text")

    @pytest.mark.asyncio
    async def test_update_pr_description_empty_description(self, repository):
        """Test updating PR description with empty description fails."""
        with pytest.raises(ValueError, match="Description cannot be empty"):
            await repository.update_pr_description("https://github.com/owner/repo/pull/123", "")

    @pytest.mark.asyncio
    async def test_update_pr_description_non_string_description(self, repository):
        """Test updating PR description with non-string description fails."""
        with pytest.raises(ValueError, match="Description must be a string"):
            await repository.update_pr_description("https://github.com/owner/repo/pull/123", 123)

    @pytest.mark.asyncio
    async def test_update_pr_description_404_error(self, repository):
        """Test updating PR description handles 404 error."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.edit.side_effect = GithubException(404, "Not found")

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        with pytest.raises(RuntimeError, match="not found"):
            await repository.update_pr_description("https://github.com/owner/repo/pull/123", "New description")

    @pytest.mark.asyncio
    async def test_update_pr_description_403_error(self, repository):
        """Test updating PR description handles 403 forbidden error."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.edit.side_effect = GithubException(403, "Forbidden")

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        with pytest.raises(RuntimeError, match="Insufficient permissions"):
            await repository.update_pr_description("https://github.com/owner/repo/pull/123", "New description")

    @pytest.mark.asyncio
    async def test_update_pr_description_rate_limit_error(self, repository):
        """Test updating PR description handles rate limit error."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.edit.side_effect = GithubException(429, "Rate limit exceeded")

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        with pytest.raises(RuntimeError, match="rate limit exceeded"):
            await repository.update_pr_description("https://github.com/owner/repo/pull/123", "New description")

    @pytest.mark.asyncio
    async def test_update_pr_description_generic_error(self, repository):
        """Test updating PR description handles generic GitHub error."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.edit.side_effect = GithubException(500, "Server error")

        repository._mock_api_client._get_pygithub_repository.return_value = mock_repo
        repository._mock_api_client._get_pygithub_pull_request.return_value = mock_pr

        with pytest.raises(RuntimeError, match="GitHub API error"):
            await repository.update_pr_description("https://github.com/owner/repo/pull/123", "New description")
