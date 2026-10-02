"""Unit tests for GitHubPRDiffRepository.

Tests covering initialization, GitHub object setup, PR approval,
description updates, and error handling.
"""

import pytest
from unittest.mock import Mock, patch
from github.GithubException import (
    GithubException,
    UnknownObjectException,
    RateLimitExceededException,
)

from prdiffer.domain.config.github_config import GitHubConfig
from prdiffer.infrastructure.github_repository import GitHubPRDiffRepository
from prdiffer.domain.exceptions import PRDifferException


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
