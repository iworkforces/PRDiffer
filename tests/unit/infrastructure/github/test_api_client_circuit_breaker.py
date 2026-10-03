from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from github import GithubException
from github.Repository import Repository
from github.PullRequest import PullRequest

from prdiffer.infrastructure.github.client import GitHubAPIClient
import prdiffer.infrastructure.utils.circuit_breaker_core as core


@pytest.mark.parametrize("health", [False, True])
@pytest.mark.parametrize("lookup", ["repository", "pull_request"])
def test_actual_sdk_wrapping_recovers_and_counts_logical_failures(monkeypatch, health, lookup):
    client = GitHubAPIClient(max_retries=3, retry_delay=0, circuit_breaker_failure_threshold=1,
                            circuit_breaker_timeout=10, api_health_tracking=health,
                            adaptive_retry_enabled=False)
    clock = [100.0]
    monkeypatch.setattr(core, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(client._retry_handler, "_calculate_retry_delay", lambda *args, **kwargs: 0)
    sdk = Mock()
    monkeypatch.setattr(client, "_github_client", sdk)
    repo = Mock(spec=Repository)
    pull = Mock(spec=PullRequest)
    backend = sdk.get_repo if lookup == "repository" else repo.get_pull
    result = repo if lookup == "repository" else pull
    backend.side_effect = GithubException(503, {"message": "server unavailable"})

    def invoke():
        if lookup == "repository":
            return client._get_pygithub_repository("owner/repo")
        return client._get_pygithub_pull_request(repo, 7)

    assert invoke() is None
    assert backend.call_count == (3 if lookup == "repository" else 4)
    assert client._retry_handler._circuit_breaker.failure_count == 1
    clock[0] = 110
    backend.side_effect = None
    backend.return_value = result
    assert invoke() is result
    assert client._retry_handler._circuit_breaker.state.value == "closed"


@pytest.mark.parametrize("status", [401, 403, 404, 422])
@pytest.mark.parametrize("lookup", ["repository", "pull_request"])
def test_actual_sdk_wrapping_excludes_permanent_errors(monkeypatch, status, lookup):
    client = GitHubAPIClient(max_retries=1, retry_delay=0, adaptive_retry_enabled=False)
    monkeypatch.setattr(client._retry_handler, "_calculate_retry_delay", lambda *args, **kwargs: 0)
    sdk = Mock()
    monkeypatch.setattr(client, "_github_client", sdk)
    repo = Mock(spec=Repository)
    backend = sdk.get_repo if lookup == "repository" else repo.get_pull
    backend.side_effect = GithubException(status, {"message": "permanent"})
    if lookup == "repository":
        result = client._get_pygithub_repository("owner/repo")
    else:
        result = client._get_pygithub_pull_request(repo, 7)
    assert result is None
    assert client._retry_handler._circuit_breaker.failure_count == 0
    assert client._retry_handler._circuit_breaker.state.value == "closed"
