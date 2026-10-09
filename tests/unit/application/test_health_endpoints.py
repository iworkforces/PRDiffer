"""Comprehensive tests for health_endpoints.py."""

import pytest
import anyio
import json
from unittest.mock import Mock, AsyncMock

from prdiffer.application.health_endpoints import HealthEndpoints
from prdiffer.application.components.authentication import AuthenticationMiddleware
from prdiffer.application.components.metrics_tracker import MetricsTracker
from prdiffer.application.components.rate_limiter import RateLimiter
from prdiffer.application.components.health_monitor import HealthMonitor
from prdiffer.infrastructure.cache.service import CacheService
from prdiffer.infrastructure.utils.coalescing_service import RequestCoalescingService
from prdiffer.domain.entities.pr_diff import PRDiff


@pytest.mark.parametrize("failed_monitor", [False, True])
async def test_public_health_real_components_redact_identifiers(monkeypatch, failed_monitor):
    # Given: real sensitive statistics and an outstanding request.
    monkeypatch.setenv("MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_API_KEYS", "privacy_api_key_12345678901")
    auth = AuthenticationMiddleware()
    auth._default_client_id = "privacy-client-identifier"
    tracker = MetricsTracker()
    limiter = RateLimiter()
    monitor = HealthMonitor(tracker, limiter)
    cache = CacheService()
    key = "github-full-diff-v3:privacy-owner:privacy-repository:987654321:privacy-base-sha:privacy-head-sha"
    await cache.set(key, "privacy-head-sha", PRDiff(files=(), head_sha="c" * 40))
    coalescer = RequestCoalescingService(logger=Mock())
    entered, release = anyio.Event(), anyio.Event()

    async def pending():
        entered.set()
        await release.wait()
        return PRDiff(files=(), head_sha="c" * 40)

    async def run_pending():
        await coalescer.coalesce(key, pending)

    if failed_monitor:
        limiter.get_rate_limit_info = Mock(side_effect=RuntimeError("privacy-monitor-distinctive-error"))
    endpoints = HealthEndpoints(monitor, tracker, cache, auth, coalescer, Mock())
    async with anyio.create_task_group() as group:
        group.start_soon(run_pending)
        await entered.wait()
        # When: zero-argument public health, without authentication.
        result = await endpoints.get_health_handler()()
        # Then: fixed aggregate keys only; internal statistics stay intact.
        assert set(result) == {
            "status",
            "uptime_seconds",
            "uptime_human",
            "total_requests",
            "successful_requests",
            "failed_requests",
            "success_rate",
            "current_rate",
            "rate_limit",
            "rate_limit_window",
            "remaining_requests",
            "authentication",
            "cache",
            "request_coalescing",
        }
        assert set(result["authentication"]) == {"authentication_enabled", "api_keys_configured", "admin_api_key_configured"}
        assert set(result["cache"]) == {
            "cache_size",
            "cache_bytes",
            "cache_max_bytes",
            "cache_hits",
            "cache_misses",
            "cache_expirations",
            "cache_evictions_ttl",
            "cache_evictions_size",
        }
        assert result["request_coalescing"] == {"pending_count": 1, "total_waiters": 1}
        assert "privacy-" not in json.dumps(result)
        assert "987654321" not in json.dumps(result)
        assert result["status"] == ("unhealthy" if failed_monitor else "healthy")
        assert tracker.get_metrics_summary()["total_requests"] == 0
        assert key in cache.get_stats()["keys"]
        assert key in (await coalescer.get_stats())["pending_keys"]
        assert auth.get_status()["default_client_id"] == "privacy-client-identifier"
        release.set()


class TestHealthEndpoints:
    """Tests for HealthEndpoints class."""

    @pytest.fixture
    def health_endpoints(self):
        """Create HealthEndpoints instance with mocked dependencies."""
        health_monitor = Mock()
        metrics_tracker = Mock()
        cache_service = Mock()
        authentication = Mock()
        request_coalescing = Mock()
        logger = Mock()

        health_endpoints = HealthEndpoints(
            health_monitor=health_monitor,
            metrics_tracker=metrics_tracker,
            cache_service=cache_service,
            authentication=authentication,
            request_coalescing=request_coalescing,
            logger=logger,
        )

        return health_endpoints

    @pytest.mark.anyio
    async def test_get_health_status_success(self, health_endpoints):
        """Test _get_health_status returns all component statuses."""
        health_endpoints._health_monitor.check_health.return_value = {"status": "healthy"}
        health_endpoints._authentication.get_status.return_value = {"enabled": True}
        health_endpoints._cache_service.get_stats.return_value = {"size": 100}
        health_endpoints._request_coalescing.get_stats = AsyncMock(return_value={"active": 5})

        status = await health_endpoints._get_health_status()

        assert status["status"] == "healthy"
        assert "authentication" in status
        assert "cache" in status
        assert "repository_cache" not in status
        assert "request_coalescing" in status

    def test_get_health_handler_returns_callable(self, health_endpoints):
        """Test get_health_handler returns a callable."""
        handler = health_endpoints.get_health_handler()

        assert callable(handler)

    @pytest.mark.anyio
    async def test_health_handler_success(self, health_endpoints):
        """Test health handler returns health status."""
        health_endpoints._health_monitor.check_health.return_value = {"status": "healthy"}
        health_endpoints._authentication.get_status.return_value = {"enabled": True}
        health_endpoints._cache_service.get_stats.return_value = {}
        health_endpoints._request_coalescing.get_stats = AsyncMock(return_value={})

        handler = health_endpoints.get_health_handler()
        result = await handler()

        assert result["status"] == "healthy"

    @pytest.mark.anyio
    async def test_health_handler_runtime_error(self, health_endpoints):
        """Test health handler handles RuntimeError."""
        health_endpoints._health_monitor.check_health.side_effect = RuntimeError("test error")

        handler = health_endpoints.get_health_handler()
        result = await handler()

        assert result["status"] == "unhealthy"
        assert result == {"status": "unhealthy"}
        health_endpoints._logger.error.assert_called()

    @pytest.mark.anyio
    async def test_health_handler_key_error(self, health_endpoints):
        """Test health handler handles KeyError."""
        health_endpoints._health_monitor.check_health.side_effect = KeyError("test key")

        handler = health_endpoints.get_health_handler()
        result = await handler()

        assert result["status"] == "unhealthy"
        health_endpoints._logger.error.assert_called()

    @pytest.mark.anyio
    async def test_health_handler_attribute_error(self, health_endpoints):
        """Test health handler handles AttributeError."""
        health_endpoints._health_monitor.check_health.side_effect = AttributeError("test attr")

        handler = health_endpoints.get_health_handler()
        result = await handler()

        assert result["status"] == "unhealthy"
        health_endpoints._logger.error.assert_called()

    def test_get_metrics_handler_returns_callable(self, health_endpoints):
        """Test get_metrics_handler returns a callable."""
        handler = health_endpoints.get_metrics_handler()

        assert callable(handler)

    @pytest.mark.anyio
    async def test_metrics_handler_success(self, health_endpoints):
        """Test metrics handler returns metrics."""
        health_endpoints._metrics_tracker.get_metrics_summary.return_value = {
            "requests": 100,
            "errors": 5,
        }

        handler = health_endpoints.get_metrics_handler()
        mock_request = Mock()
        result = await handler(mock_request)

        assert result.status_code == 200

    @pytest.mark.anyio
    async def test_metrics_handler_runtime_error(self, health_endpoints):
        """Test metrics handler handles RuntimeError."""
        health_endpoints._metrics_tracker.get_metrics_summary.side_effect = RuntimeError("test")

        handler = health_endpoints.get_metrics_handler()
        mock_request = Mock()
        result = await handler(mock_request)

        assert result.status_code == 500
        health_endpoints._logger.error.assert_called()

    @pytest.mark.anyio
    async def test_metrics_handler_key_error(self, health_endpoints):
        """Test metrics handler handles KeyError."""
        health_endpoints._metrics_tracker.get_metrics_summary.side_effect = KeyError("test")

        handler = health_endpoints.get_metrics_handler()
        mock_request = Mock()
        result = await handler(mock_request)

        assert result.status_code == 500

    @pytest.mark.anyio
    async def test_metrics_handler_attribute_error(self, health_endpoints):
        """Test metrics handler handles AttributeError."""
        health_endpoints._metrics_tracker.get_metrics_summary.side_effect = AttributeError("test")

        handler = health_endpoints.get_metrics_handler()
        mock_request = Mock()
        result = await handler(mock_request)

        assert result.status_code == 500


class TestCreateSafeErrorMessage:
    """Tests for _create_safe_error_message method."""

    @pytest.fixture
    def health_endpoints(self):
        """Create HealthEndpoints instance with mocked dependencies."""
        return HealthEndpoints(
            health_monitor=Mock(),
            metrics_tracker=Mock(),
            cache_service=Mock(),
            authentication=Mock(),
            request_coalescing=Mock(),
            logger=Mock(),
        )

    def _make_error(self, class_name):
        """Create an error with a specific class name."""

        class CustomError(Exception):
            pass

        CustomError.__name__ = class_name
        return CustomError()

    def test_github_exception(self, health_endpoints):
        """Test safe message for GithubException."""
        error = self._make_error("GithubException")
        message = health_endpoints._create_safe_error_message(error)
        assert message == "GitHub API error occurred"

    def test_rate_limit_exception(self, health_endpoints):
        """Test safe message for RateLimitExceededException."""
        error = self._make_error("RateLimitExceededException")
        message = health_endpoints._create_safe_error_message(error)
        assert "rate limit" in message.lower()

    def test_unknown_object_exception(self, health_endpoints):
        """Test safe message for UnknownObjectException."""
        error = self._make_error("UnknownObjectException")
        message = health_endpoints._create_safe_error_message(error)
        assert "not found" in message.lower()

    def test_bad_credentials_exception(self, health_endpoints):
        """Test safe message for BadCredentialsException."""
        error = self._make_error("BadCredentialsException")
        message = health_endpoints._create_safe_error_message(error)
        assert "authentication" in message.lower()

    def test_two_factor_exception(self, health_endpoints):
        """Test safe message for TwoFactorException."""
        error = self._make_error("TwoFactorException")
        message = health_endpoints._create_safe_error_message(error)
        assert "two-factor" in message.lower()

    def test_invalid_url_error(self, health_endpoints):
        """Test safe message for InvalidURLError."""
        error = self._make_error("InvalidURLError")
        message = health_endpoints._create_safe_error_message(error)
        assert "url" in message.lower()

    def test_invalid_repository_error(self, health_endpoints):
        """Test safe message for InvalidRepositoryError."""
        error = self._make_error("InvalidRepositoryError")
        message = health_endpoints._create_safe_error_message(error)
        assert "repository" in message.lower()

    def test_invalid_pr_number_error(self, health_endpoints):
        """Test safe message for InvalidPRNumberError."""
        error = self._make_error("InvalidPRNumberError")
        message = health_endpoints._create_safe_error_message(error)
        assert "pull request" in message.lower()

    def test_input_sanitization_error(self, health_endpoints):
        """Test safe message for InputSanitizationError."""
        error = self._make_error("InputSanitizationError")
        message = health_endpoints._create_safe_error_message(error)
        assert "input" in message.lower()

    def test_suspicious_operation_error(self, health_endpoints):
        """Test safe message for SuspiciousOperationError."""
        error = self._make_error("SuspiciousOperationError")
        message = health_endpoints._create_safe_error_message(error)
        assert "suspicious" in message.lower()

    def test_connection_error(self, health_endpoints):
        """Test safe message for ConnectionError."""
        error = ConnectionError()
        message = health_endpoints._create_safe_error_message(error)
        assert "connection" in message.lower()

    def test_timeout_error(self, health_endpoints):
        """Test safe message for TimeoutError."""
        error = TimeoutError()
        message = health_endpoints._create_safe_error_message(error)
        assert "timed out" in message.lower()

    def test_value_error(self, health_endpoints):
        """Test safe message for ValueError."""
        error = ValueError()
        message = health_endpoints._create_safe_error_message(error)
        assert "value" in message.lower()

    def test_type_error(self, health_endpoints):
        """Test safe message for TypeError."""
        error = TypeError()
        message = health_endpoints._create_safe_error_message(error)
        assert "type" in message.lower()

    def test_key_error(self, health_endpoints):
        """Test safe message for KeyError."""
        error = KeyError()
        message = health_endpoints._create_safe_error_message(error)
        assert "field" in message.lower()

    def test_attribute_error(self, health_endpoints):
        """Test safe message for AttributeError."""
        error = AttributeError()
        message = health_endpoints._create_safe_error_message(error)
        assert "configuration" in message.lower()

    def test_unknown_error_type(self, health_endpoints):
        """Test safe message for unknown error type."""
        error = Exception("unknown error")
        message = health_endpoints._create_safe_error_message(error)
        assert message == "Request processing failed"
