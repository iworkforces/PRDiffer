import pytest
from unittest.mock import MagicMock, patch
from github import GithubException

from prdiffer.infrastructure.github.client import (
    GitHubAPIClient,
    get_github_api_client,
)
from prdiffer.infrastructure.github.client_models import GITHUB_API_EXCEPTIONS
from prdiffer.domain.exceptions import PRDifferException


@pytest.fixture
def api_client():
    client = GitHubAPIClient()
    client.initialize_client()
    return client


@pytest.fixture
def api_client_no_init():
    return GitHubAPIClient()


class TestGitHubAPIClientInit:
    def test_init_defaults(self):
        client = GitHubAPIClient()

        assert client._github_client is None
        assert client._retry_handler is not None

    def test_init_with_custom_logger(self):
        mock_logger = MagicMock()
        client = GitHubAPIClient(logger=mock_logger)

        assert client._logger is mock_logger

    def test_init_simple_retry_handler(self):
        client = GitHubAPIClient(use_advanced_retry=False)

        assert client._retry_handler is not None

    def test_init_advanced_retry_handler(self):
        client = GitHubAPIClient(use_advanced_retry=True)

        assert client._retry_handler is not None


class TestInitializeClient:
    def test_initialize_with_token(self):
        client = GitHubAPIClient()
        client.initialize_client(github_token="test_token", timeout=60)

        assert client._github_client is not None

    def test_initialize_without_token(self):
        client = GitHubAPIClient()
        client.initialize_client(github_token=None, timeout=30)

        assert client._github_client is not None

    def test_reinitialize(self):
        client = GitHubAPIClient()
        client.initialize_client(github_token="token1", timeout=30)
        first_client = client._github_client

        client.initialize_client(github_token="token2", timeout=60)

        assert client._github_client is not first_client


class TestGetPyGithubRepository:
    def test_internal_get_repository_without_init_raises(self, api_client_no_init):
        with pytest.raises(PRDifferException, match="GitHub client not initialized"):
            api_client_no_init._get_pygithub_repository("owner/repo")

    def test_internal_get_repository_success(self, api_client):
        with patch.object(api_client._retry_handler, "execute_with_retry") as mock_retry:
            mock_repo = MagicMock()
            mock_retry.return_value = mock_repo

            result = api_client._get_pygithub_repository("owner/repo")

            assert result is mock_repo

    def test_internal_get_repository_error(self, api_client):
        with patch.object(api_client._retry_handler, "execute_with_retry") as mock_retry:
            mock_retry.side_effect = GithubException(403, "Forbidden", {})

            result = api_client._get_pygithub_repository("owner/repo")

            assert result is None


class TestGetPyGithubPullRequest:
    def test_internal_get_pr_success(self, api_client):
        mock_repo = MagicMock()
        with patch.object(api_client._retry_handler, "execute_with_retry") as mock_retry:
            mock_pr = MagicMock()
            mock_retry.return_value = mock_pr

            result = api_client._get_pygithub_pull_request(mock_repo, 123)

            assert result is mock_pr

    def test_internal_get_pr_error(self, api_client):
        mock_repo = MagicMock()
        with patch.object(api_client._retry_handler, "execute_with_retry") as mock_retry:
            mock_retry.side_effect = GithubException(404, "Not Found", {})

            result = api_client._get_pygithub_pull_request(mock_repo, 999)

            assert result is None


class TestGetGitHubApiClient:
    def test_factory_defaults(self):
        client = get_github_api_client()

        assert client is not None
        assert isinstance(client, GitHubAPIClient)

    def test_factory_custom_params(self):
        client = get_github_api_client(
            max_retries=5,
            retry_delay=2.0,
            timeout=60,
            circuit_breaker_enabled=False,
        )

        assert client is not None

    def test_factory_with_none_rate_limit_params(self):
        client = get_github_api_client(
            rate_limit_remaining_threshold=None,
            rate_limit_reset_buffer=None,
            secondary_rate_limit_backoff=None,
        )

        assert client is not None

    def test_factory_simple_retry(self):
        client = get_github_api_client(use_advanced_retry=False)

        assert client._retry_handler._circuit_breaker is None


class TestGithubApiExceptions:
    def test_github_api_exceptions_tuple(self):
        assert GithubException in GITHUB_API_EXCEPTIONS
        assert TimeoutError in GITHUB_API_EXCEPTIONS
        assert ConnectionError in GITHUB_API_EXCEPTIONS
        assert OSError in GITHUB_API_EXCEPTIONS
        assert RuntimeError in GITHUB_API_EXCEPTIONS
        assert ValueError in GITHUB_API_EXCEPTIONS
        assert TypeError in GITHUB_API_EXCEPTIONS
