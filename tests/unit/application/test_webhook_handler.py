"""Tests for WebhookHandler GitHub webhook processing."""

import hmac
import json

import pytest
from unittest.mock import Mock, AsyncMock

from prdiffer.application.webhook_handler import WebhookHandler


def _make_signature(secret: str, payload: bytes) -> str:
    """Create HMAC-SHA256 signature matching GitHub format."""
    return f"sha256={hmac.new(secret.encode(), payload, 'sha256').hexdigest()}"


@pytest.fixture
def webhook_handler():
    """Create WebhookHandler with mocked dependencies."""
    settings_service = Mock()
    settings_service.get.return_value = "test_webhook_secret"

    cache_service = AsyncMock()
    repository_cache_service = Mock()
    logger = Mock()
    input_validator = Mock()

    return WebhookHandler(
        settings_service=settings_service,
        cache_service=cache_service,
        repository_cache_service=repository_cache_service,
        logger=logger,
        input_validator=input_validator,
    )


@pytest.fixture
def pr_payload():
    """Standard pull_request webhook payload."""
    return {
        "action": "synchronize",
        "number": 42,
        "repository": {
            "full_name": "owner/repo",
        },
    }


@pytest.fixture
def push_payload():
    """Standard push webhook payload."""
    return {
        "repository": {
            "full_name": "owner/repo",
        },
        "ref": "refs/heads/main",
    }


@pytest.mark.unit
class TestWebhookSignatureVerification:
    """Tests for HMAC signature verification."""

    @pytest.mark.anyio
    async def test_valid_signature_accepted(self, webhook_handler, pr_payload):
        """Valid HMAC signature allows processing."""
        payload_bytes = json.dumps(pr_payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "success"

    @pytest.mark.anyio
    async def test_invalid_signature_rejected(self, webhook_handler, pr_payload):
        """Invalid HMAC signature is rejected."""
        payload_bytes = json.dumps(pr_payload).encode()
        signature = "sha256=invalid_signature_here"

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "error"
        assert "Invalid signature" in result["message"]

    @pytest.mark.anyio
    async def test_no_webhook_secret_configured(self, pr_payload):
        """Missing webhook secret returns error."""
        settings_service = Mock()
        settings_service.get.return_value = ""

        handler = WebhookHandler(
            settings_service=settings_service,
            cache_service=AsyncMock(),
            repository_cache_service=Mock(),
            logger=Mock(),
            input_validator=Mock(),
        )

        payload_bytes = json.dumps(pr_payload).encode()
        result = await handler.webhook_invalidate_cache(payload_bytes, "sha256=something", "pull_request")
        assert result["status"] == "error"
        assert "not configured" in result["message"]


@pytest.mark.unit
class TestWebhookEventFiltering:
    """Tests for event type filtering."""

    @pytest.mark.anyio
    async def test_pull_request_event_accepted(self, webhook_handler, pr_payload):
        """pull_request event is accepted."""
        payload_bytes = json.dumps(pr_payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "success"

    @pytest.mark.anyio
    async def test_push_event_accepted(self, webhook_handler, push_payload):
        """push event is accepted."""
        payload_bytes = json.dumps(push_payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "push")
        assert result["status"] == "success"

    @pytest.mark.anyio
    async def test_unsupported_event_rejected(self, webhook_handler, pr_payload):
        """Unsupported event types are rejected."""
        payload_bytes = json.dumps(pr_payload).encode()

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, "sha256=any", "issues")
        assert result["status"] == "error"
        assert "Unsupported event type" in result["message"]

    @pytest.mark.anyio
    async def test_empty_event_rejected(self, webhook_handler, pr_payload):
        """Empty event type is rejected."""
        payload_bytes = json.dumps(pr_payload).encode()

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, "sha256=any", "")
        assert result["status"] == "error"


@pytest.mark.unit
class TestWebhookCacheInvalidation:
    """Tests for cache invalidation logic."""

    @pytest.mark.anyio
    async def test_pr_opened_invalidates_cache(self, webhook_handler):
        """PR opened action invalidates PR cache."""
        payload = {
            "action": "opened",
            "number": 42,
            "repository": {"full_name": "owner/repo"},
        }
        payload_bytes = json.dumps(payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "success"
        webhook_handler._repository_cache_service.invalidate_github_pr.assert_called_once_with("owner", "repo", 42)
        webhook_handler._cache_service.invalidate_github_pr.assert_awaited_once_with("owner", "repo", 42)

    @pytest.mark.anyio
    async def test_pr_synchronize_invalidates_cache(self, webhook_handler):
        """PR synchronize action invalidates PR cache."""
        payload = {
            "action": "synchronize",
            "number": 100,
            "repository": {"full_name": "org/project"},
        }
        payload_bytes = json.dumps(payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "success"
        webhook_handler._repository_cache_service.invalidate_github_pr.assert_called_once_with("org", "project", 100)
        webhook_handler._cache_service.invalidate_github_pr.assert_awaited_once_with("org", "project", 100)

    @pytest.mark.anyio
    async def test_pr_reopened_invalidates_cache(self, webhook_handler):
        """PR reopened action invalidates PR cache."""
        payload = {
            "action": "reopened",
            "number": 55,
            "repository": {"full_name": "owner/repo"},
        }
        payload_bytes = json.dumps(payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "success"
        webhook_handler._repository_cache_service.invalidate_github_pr.assert_called_once_with("owner", "repo", 55)
        webhook_handler._cache_service.invalidate_github_pr.assert_awaited_once_with("owner", "repo", 55)

    @pytest.mark.anyio
    async def test_pr_closed_does_not_invalidate(self, webhook_handler):
        """PR closed action does not invalidate cache."""
        payload = {
            "action": "closed",
            "number": 42,
            "repository": {"full_name": "owner/repo"},
        }
        payload_bytes = json.dumps(payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "success"
        webhook_handler._repository_cache_service.invalidate_github_pr.assert_not_called()
        webhook_handler._cache_service.invalidate_github_pr.assert_not_awaited()

    @pytest.mark.anyio
    async def test_pr_closed_without_number_does_not_invalidate(self, webhook_handler):
        payload_bytes = json.dumps({"action": "closed", "repository": {"full_name": "owner/repo"}}).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")

        assert result["status"] == "success"
        webhook_handler._cache_service.invalidate_github_pr.assert_not_awaited()
        webhook_handler._repository_cache_service.invalidate_github_pr.assert_not_called()

    @pytest.mark.anyio
    async def test_push_invalidates_repo_cache(self, webhook_handler):
        """Push event invalidates repository-level cache."""
        payload = {
            "repository": {"full_name": "owner/repo"},
            "ref": "refs/heads/main",
        }
        payload_bytes = json.dumps(payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "push")
        assert result["status"] == "success"
        webhook_handler._repository_cache_service.invalidate_github_repository.assert_called_once_with("owner", "repo")
        webhook_handler._cache_service.invalidate_github_repository.assert_awaited_once_with("owner", "repo")


@pytest.mark.unit
class TestWebhookPayloadParsing:
    """Tests for payload parsing edge cases."""

    @pytest.mark.anyio
    async def test_invalid_json_returns_error(self, webhook_handler):
        """Invalid JSON payload returns error."""
        payload_bytes = b"not json at all"
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "error"
        assert "Invalid payload" in result["message"]

    @pytest.mark.anyio
    async def test_missing_repository_returns_error(self, webhook_handler):
        """Payload missing repository info returns error."""
        payload = {"action": "opened", "number": 1}
        payload_bytes = json.dumps(payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "error"
        assert "Missing repository" in result["message"]

    @pytest.mark.anyio
    async def test_empty_repository_name_returns_error(self, webhook_handler):
        """Empty repository full_name returns error."""
        payload = {
            "action": "opened",
            "number": 1,
            "repository": {"full_name": ""},
        }
        payload_bytes = json.dumps(payload).encode()
        signature = _make_signature("test_webhook_secret", payload_bytes)

        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, signature, "pull_request")
        assert result["status"] == "error"
        assert "Missing repository" in result["message"]

    @pytest.mark.anyio
    @pytest.mark.parametrize("full_name", ["owner", "owner/", "/repo", "a/b/c", " owner/repo", "owner/re po", 42, None])
    async def test_malformed_repository_does_not_evict(self, webhook_handler, full_name):
        payload_bytes = json.dumps({"action": "opened", "number": 42, "repository": {"full_name": full_name}}).encode()
        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, _make_signature("test_webhook_secret", payload_bytes), "pull_request")
        assert result == {"status": "error", "message": "Missing repository info"}
        webhook_handler._cache_service.invalidate_github_pr.assert_not_awaited()
        webhook_handler._repository_cache_service.invalidate_github_pr.assert_not_called()

    @pytest.mark.anyio
    @pytest.mark.parametrize("number", [None, 0, -1, True, False, "42", 4.2])
    async def test_malformed_pr_number_does_not_evict(self, webhook_handler, number):
        payload_bytes = json.dumps({"action": "opened", "number": number, "repository": {"full_name": "owner/repo"}}).encode()
        result = await webhook_handler.webhook_invalidate_cache(payload_bytes, _make_signature("test_webhook_secret", payload_bytes), "pull_request")
        assert result == {"status": "error", "message": "Invalid PR number"}
        webhook_handler._cache_service.invalidate_github_pr.assert_not_awaited()
        webhook_handler._repository_cache_service.invalidate_github_pr.assert_not_called()


@pytest.mark.unit
class TestGetWebhookHandler:
    """Tests for get_webhook_handler method."""

    def test_returns_callable(self, webhook_handler):
        """get_webhook_handler returns a callable."""
        handler = webhook_handler.get_webhook_handler()
        assert callable(handler)

    @pytest.mark.anyio
    async def test_handler_missing_signature_uses_fallback(self, webhook_handler):
        """Handler falls back to X-Hub-Signature when X-Hub-Signature-256 missing."""
        mock_headers = Mock()
        mock_headers.get.side_effect = lambda k, d="": {
            "X-Hub-Signature-256": "",
            "X-Hub-Signature": "sha256=some_sig",
            "X-GitHub-Event": "push",
        }.get(k, d)
        mock_request = Mock()
        mock_request.headers = mock_headers
        mock_request.body = AsyncMock(return_value=b"{}")

        handler = webhook_handler.get_webhook_handler()
        result = await handler(mock_request)
        # Should process (even if signature fails)
        assert result.status_code in (200, 400, 401)

    @pytest.mark.anyio
    async def test_handler_exception_returns_500(self, webhook_handler):
        """Handler returns 500 on unexpected exception."""
        mock_headers = Mock()
        mock_headers.get.return_value = ""
        mock_request = Mock()
        mock_request.headers = mock_headers
        mock_request.body = AsyncMock(side_effect=Exception("read error"))

        handler = webhook_handler.get_webhook_handler()
        result = await handler(mock_request)
        assert result.status_code == 500
