"""Tests for webhook cache invalidation with HMAC verification."""

import json
import hmac
from collections.abc import Mapping

import pytest
from unittest.mock import Mock, patch, AsyncMock
from starlette.requests import Request

from prdiffer.application.mcp_server import FastMCPServer
from prdiffer.application.provider_resolver import ProviderCapabilityResolver
from prdiffer.application.webhook_handler import WebhookHandler
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import github_full_diff_v3_identity, gitlab_full_diff_v1_identity
from prdiffer.infrastructure.cache.service import CacheService
from prdiffer.infrastructure.cache.cache_repository import RepositoryCacheService


@pytest.fixture
def mock_cache_service():
    """Create a mock cache service."""
    mock = Mock()
    mock.invalidate = AsyncMock()
    mock.invalidate_github_pr = AsyncMock()
    mock.invalidate_github_repository = AsyncMock()
    return mock


@pytest.fixture
def mock_repository_cache_service():
    """Create a mock repository cache service."""
    mock = Mock()
    mock.invalidate = Mock(return_value=True)
    mock.invalidate_github_pr = Mock()
    mock.invalidate_github_repository = Mock()
    return mock


@pytest.fixture
def mock_settings():
    """Create a mock settings service."""
    mock = Mock()
    mock.get = Mock(return_value="test_webhook_secret")
    return mock


@pytest.fixture
def mcp_server(mock_cache_service, mock_repository_cache_service, mock_settings):
    """Create an MCP server instance with mocked dependencies."""
    mock_pr_diff_service = Mock()
    mock_logger = Mock()
    mock_rate_limiter = Mock()
    mock_metrics_tracker = Mock()
    mock_pr_operation_handler = Mock()
    mock_health_monitor = Mock()
    mock_server_configuration = Mock()
    mock_server_configuration.setup_logging = Mock()
    mock_server_configuration.get_mcp_instructions = Mock(return_value="Test instructions")

    server = FastMCPServer(
        settings_service=mock_settings,
        cache_service=mock_cache_service,
        repository_cache_service=mock_repository_cache_service,
        pr_diff_service=mock_pr_diff_service,
        logger=mock_logger,
        provider_resolver=ProviderCapabilityResolver(),
        rate_limiter=mock_rate_limiter,
        metrics_tracker=mock_metrics_tracker,
        pr_operation_handler=mock_pr_operation_handler,
        health_monitor=mock_health_monitor,
        server_configuration=mock_server_configuration,
    )
    return server


@pytest.mark.unit
class TestWebhookCacheInvalidation:
    """Test webhook cache invalidation functionality."""

    @pytest.mark.asyncio
    async def test_webhook_invalidates_pr_on_opened_event(self, mcp_server, mock_cache_service, mock_repository_cache_service):
        """Test that PR cache is invalidated on opened event."""

        webhook_secret = "test_webhook_secret"
        payload = {
            "action": "opened",
            "repository": {"full_name": "owner/repo"},
            "number": 123,
        }
        payload_bytes = json.dumps(payload).encode("utf-8")
        signature = f"sha256={hmac.new(webhook_secret.encode(), payload_bytes, 'sha256').hexdigest()}"

        result = await mcp_server._webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")

        assert result["status"] == "success"
        mock_repository_cache_service.invalidate_github_pr.assert_called_once_with("owner", "repo", 123)
        mock_cache_service.invalidate_github_pr.assert_awaited_once_with("owner", "repo", 123)

    @pytest.mark.asyncio
    async def test_webhook_invalidates_pr_on_synchronize_event(self, mcp_server, mock_cache_service, mock_repository_cache_service):
        """Test that PR cache is invalidated on synchronize event."""

        webhook_secret = "test_webhook_secret"
        payload = {
            "action": "synchronize",
            "repository": {"full_name": "owner/repo"},
            "number": 456,
        }
        payload_bytes = json.dumps(payload).encode("utf-8")
        signature = f"sha256={hmac.new(webhook_secret.encode(), payload_bytes, 'sha256').hexdigest()}"

        result = await mcp_server._webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")

        assert result["status"] == "success"
        mock_repository_cache_service.invalidate_github_pr.assert_called_once_with("owner", "repo", 456)
        mock_cache_service.invalidate_github_pr.assert_awaited_once_with("owner", "repo", 456)

    @pytest.mark.asyncio
    async def test_webhook_invalidates_repo_on_push_event(self, mcp_server, mock_cache_service, mock_repository_cache_service):
        """Test that both cache services are invalidated on push event."""

        webhook_secret = "test_webhook_secret"
        payload = {
            "action": "push",
            "repository": {"full_name": "owner/repo"},
        }
        payload_bytes = json.dumps(payload).encode("utf-8")
        signature = f"sha256={hmac.new(webhook_secret.encode(), payload_bytes, 'sha256').hexdigest()}"

        result = await mcp_server._webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "push")

        assert result["status"] == "success"
        mock_repository_cache_service.invalidate_github_repository.assert_called_once_with("owner", "repo")
        mock_cache_service.invalidate_github_repository.assert_awaited_once_with("owner", "repo")

    @pytest.mark.asyncio
    async def test_webhook_returns_error_for_missing_secret(self, mcp_server, mock_repository_cache_service, mock_settings):
        """Test that webhook returns error when secret not configured."""
        mock_settings.get = Mock(return_value="")
        payload = {"action": "opened", "repository": {"full_name": "owner/repo"}}
        payload_bytes = json.dumps(payload).encode("utf-8")

        result = await mcp_server._webhook_handler.webhook_invalidate_cache(payload_bytes, "sha256=valid", "pull_request")

        assert result["status"] == "error"
        assert result["message"] == "Webhook secret not configured"
        mock_repository_cache_service.invalidate_github_pr.assert_not_called()

    @pytest.mark.asyncio
    async def test_webhook_returns_error_for_missing_repository(self, mcp_server, mock_repository_cache_service):
        """Test that webhook returns error when repository info missing."""

        webhook_secret = "test_webhook_secret"
        payload = {"action": "opened", "repository": {}}
        payload_bytes = json.dumps(payload).encode("utf-8")
        signature = f"sha256={hmac.new(webhook_secret.encode(), payload_bytes, 'sha256').hexdigest()}"

        result = await mcp_server._webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")

        assert result["status"] == "error"
        assert result["message"] in ["Missing repository info", "Invalid signature"]
        mock_repository_cache_service.invalidate_github_pr.assert_not_called()

    @pytest.mark.asyncio
    async def test_webhook_returns_error_for_unsupported_event(self, mcp_server, mock_repository_cache_service):
        """Test that webhook returns error for unsupported event types."""
        payload = {"action": "unknown_event", "repository": {"full_name": "owner/repo"}}
        payload_bytes = json.dumps(payload).encode("utf-8")
        signature = "sha256=valid_signature"

        result = await mcp_server._webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")

        assert result["status"] == "error"
        assert result["message"] in ["Unsupported event type", "Invalid signature"]
        mock_repository_cache_service.invalidate_github_pr.assert_not_called()

    @pytest.mark.asyncio
    async def test_webhook_validates_hmac_signature(self, mcp_server, mock_repository_cache_service):
        """Test that webhook validates HMAC signature."""

        webhook_secret = "test_webhook_secret"
        payload_data = {"action": "opened", "number": 123, "repository": {"full_name": "owner/repo"}}
        payload_bytes = json.dumps(payload_data).encode("utf-8")

        expected_signature = f"sha256={hmac.new(webhook_secret.encode(), payload_bytes, 'sha256').hexdigest()}"

        with patch.object(mcp_server, "_settings_service") as mock_settings:
            mock_settings.get = Mock(return_value=webhook_secret)

            result = await mcp_server._webhook_handler.webhook_invalidate_cache(payload_bytes, expected_signature, "pull_request")

            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_webhook_rejects_invalid_signature(self, mcp_server, mock_repository_cache_service):
        """Test that webhook rejects invalid HMAC signature."""

        payload = {
            "action": "opened",
            "repository": {"full_name": "owner/repo"},
            "number": 123,
        }
        payload_bytes = json.dumps(payload).encode("utf-8")
        invalid_signature = "sha256=invalid_signature_here"

        result = await mcp_server._webhook_handler.webhook_invalidate_cache(payload_bytes, invalid_signature, "pull_request")

        assert result["status"] == "error"
        assert result["message"] == "Invalid signature"
        mock_repository_cache_service.invalidate_github_pr.assert_not_called()


@pytest.mark.unit
class TestWebhookHTTPHandler:
    """Test webhook HTTP endpoint handler."""

    @pytest.mark.asyncio
    async def test_webhook_http_endpoint_calls_invalidate(self, mcp_server, mock_cache_service, mock_repository_cache_service):
        """Test that HTTP endpoint calls invalidate cache method."""

        webhook_secret = "test_webhook_secret"
        payload = {
            "action": "opened",
            "repository": {"full_name": "owner/repo"},
            "number": 123,
        }
        payload_bytes = json.dumps(payload).encode("utf-8")
        signature = f"sha256={hmac.new(webhook_secret.encode(), payload_bytes, 'sha256').hexdigest()}"

        mock_request = Mock()
        mock_request.headers = {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "pull_request",
        }
        mock_request.body = AsyncMock(return_value=payload_bytes)

        handler = mcp_server._webhook_handler.get_webhook_handler()
        response = await handler(mock_request)

        assert response.status_code == 200
        mock_repository_cache_service.invalidate_github_pr.assert_called_once_with("owner", "repo", 123)
        mock_cache_service.invalidate_github_pr.assert_awaited_once_with("owner", "repo", 123)

    @pytest.mark.asyncio
    async def test_webhook_http_endpoint_handles_invalid_json(self, mcp_server):
        """Test that HTTP endpoint handles invalid JSON payload."""
        webhook_secret = "test_webhook_secret"
        invalid_payload_bytes = b"invalid json"
        signature = f"sha256={hmac.new(webhook_secret.encode(), invalid_payload_bytes, 'sha256').hexdigest()}"

        mock_request = Mock()
        mock_request.headers = {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "pull_request",
        }
        mock_request.body = AsyncMock(return_value=invalid_payload_bytes)

        handler = mcp_server._webhook_handler.get_webhook_handler()
        response = await handler(mock_request)

        assert response.status_code == 400
        assert "Invalid" in response.body.decode()

    @pytest.mark.asyncio
    async def test_webhook_http_endpoint_handles_exceptions(self, mcp_server, mock_repository_cache_service):
        """Test that HTTP endpoint handles general exceptions gracefully."""
        mock_request = Mock()
        mock_request.headers = {
            "X-Hub-Signature-256": "sha256=valid",
            "X-GitHub-Event": "pull_request",
        }
        mock_request.body = AsyncMock(side_effect=Exception("Unexpected error"))

        handler = mcp_server._webhook_handler.get_webhook_handler()
        response = await handler(mock_request)

        assert response.status_code == 500
        assert "Internal server error" in response.body.decode()
        mock_repository_cache_service.invalidate_github_pr.assert_not_called()


def _signed_request(payload: Mapping[str, object], event: str, *, valid_signature: bool = True, fallback: bool = False) -> Request:
    raw_body = json.dumps(payload).encode()
    signature = f"sha256={hmac.new(b'test_webhook_secret', raw_body, 'sha256').hexdigest()}" if valid_signature else "sha256=invalid"
    signature_header = b"x-hub-signature" if fallback else b"x-hub-signature-256"
    headers = [(signature_header, signature.encode()), (b"x-github-event", event.encode())]

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/webhook", "headers": headers}, receive)


@pytest.fixture
def real_webhook_caches() -> tuple[WebhookHandler, CacheService, RepositoryCacheService]:
    diff_cache = CacheService()
    repository_cache = RepositoryCacheService()
    settings = Mock()
    settings.get.return_value = "test_webhook_secret"
    return WebhookHandler(settings, diff_cache, repository_cache, Mock(), Mock()), diff_cache, repository_cache


async def _seed_webhook_caches(diff_cache: CacheService, repository_cache: RepositoryCacheService) -> dict[str, tuple[str, str, PRDiff]]:
    identities = {
        "first_version": github_full_diff_v3_identity("owner", "repo", 42, "base-1", "head-1"),
        "second_version": github_full_diff_v3_identity("owner", "repo", 42, "base-2", "head-2"),
        "other_pr": github_full_diff_v3_identity("owner", "repo", 43, "base-3", "head-3"),
        "other_repo": github_full_diff_v3_identity("owner", "other", 42, "base-4", "head-4"),
        "gitlab": gitlab_full_diff_v1_identity("owner", "repo", 42, 1, "base", "start", "head"),
    }
    entries = {label: (identity.cache_key, identity.validation_token, PRDiff()) for label, identity in identities.items()}
    entries.update({label: (key, "legacy-token", PRDiff()) for label, key in {
        "legacy": "owner/repo/pr/42",
        "legacy_other_pr": "owner/repo/pr/43",
        "legacy_other_repo": "owner/other/pr/42",
    }.items()})
    for key, token, value in entries.values():
        await diff_cache.set(key, token, value)
    for owner, repo, number in [("owner", "repo", 42), ("owner", "repo", 43), ("owner", "other", 42)]:
        repository_cache.insert(Mock(repo_owner=owner, repo_name=repo, pr_number=number, _initialized=True))
    return entries


async def _assert_entries(diff_cache: CacheService, entries: dict[str, tuple[str, str, PRDiff]], removed: set[str]) -> None:
    for label, (key, token, value) in entries.items():
        assert await diff_cache.get(key, token) is (None if label in removed else value), label


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened"])
async def test_signed_pr_http_evicts_all_target_versions_only(real_webhook_caches, action):
    handler, diff_cache, repository_cache = real_webhook_caches
    entries = await _seed_webhook_caches(diff_cache, repository_cache)
    endpoint = handler.get_webhook_handler()
    payload = {"action": action, "number": 42, "repository": {"full_name": "owner/repo"}}
    assert (await endpoint(_signed_request(payload, "pull_request", fallback=True))).status_code == 200
    removed = {"first_version", "second_version", "legacy"}
    await _assert_entries(diff_cache, entries, removed)
    assert repository_cache.retrieve("owner", "repo", 42) is None
    assert repository_cache.retrieve("owner", "repo", 43) is not None
    assert repository_cache.retrieve("owner", "other", 42) is not None
    assert (await endpoint(_signed_request(payload, "pull_request"))).status_code == 200
    await _assert_entries(diff_cache, entries, removed)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_signed_push_http_evicts_repository_only(real_webhook_caches):
    handler, diff_cache, repository_cache = real_webhook_caches
    entries = await _seed_webhook_caches(diff_cache, repository_cache)
    endpoint = handler.get_webhook_handler()
    payload = {"repository": {"full_name": "owner/repo"}}
    assert (await endpoint(_signed_request(payload, "push"))).status_code == 200
    removed = {"first_version", "second_version", "other_pr", "legacy", "legacy_other_pr"}
    await _assert_entries(diff_cache, entries, removed)
    assert repository_cache.retrieve("owner", "repo", 42) is None
    assert repository_cache.retrieve("owner", "repo", 43) is None
    assert repository_cache.retrieve("owner", "other", 42) is not None
    assert (await endpoint(_signed_request(payload, "push"))).status_code == 200
    await _assert_entries(diff_cache, entries, removed)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_signed_closed_pr_without_number_http_preserves_caches(real_webhook_caches):
    handler, diff_cache, repository_cache = real_webhook_caches
    entries = await _seed_webhook_caches(diff_cache, repository_cache)
    payload = {"action": "closed", "repository": {"full_name": "owner/repo"}}

    response = await handler.get_webhook_handler()(_signed_request(payload, "pull_request"))

    assert response.status_code == 200
    await _assert_entries(diff_cache, entries, set())
    assert repository_cache.retrieve("owner", "repo", 42) is not None
    assert repository_cache.retrieve("owner", "repo", 43) is not None
    assert repository_cache.retrieve("owner", "other", 42) is not None


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "event", "status"),
    [
        ({"action": "opened", "number": 42, "repository": {"full_name": "owner/repo"}}, "pull_request", 401),
        ({"action": "closed", "number": 42, "repository": {"full_name": "owner/repo"}}, "pull_request", 200),
        ({"action": "opened", "number": True, "repository": {"full_name": "owner/repo"}}, "pull_request", 400),
        ({"action": "opened", "number": 42, "repository": {"full_name": "owner/repo/extra"}}, "pull_request", 400),
        ({"repository": {"full_name": "owner"}}, "push", 400),
    ],
)
async def test_http_rejections_and_unsupported_action_preserve_real_cache(real_webhook_caches, payload, event, status):
    handler, diff_cache, repository_cache = real_webhook_caches
    entries = await _seed_webhook_caches(diff_cache, repository_cache)
    response = await handler.get_webhook_handler()(_signed_request(payload, event, valid_signature=status != 401))
    assert response.status_code == status
    await _assert_entries(diff_cache, entries, set())
    assert repository_cache.size() == 3
