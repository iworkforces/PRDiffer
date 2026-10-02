"""Comprehensive tests for retry_handler.py uncovered lines."""

import pytest
import time
from unittest.mock import Mock, patch

from prdiffer.infrastructure.utils.retry.handler import (
    UnifiedRetryHandler,
)
from prdiffer.infrastructure.utils.retry.models import (
    OperationContext,
)
from prdiffer.infrastructure.utils.retry.factories import get_retry_handler
from prdiffer.infrastructure.utils.circuit_breaker_core import CircuitState
from prdiffer.infrastructure.utils.rate_limit_parser import RateLimitInfo


class TestCircuitBreakerIntegration:
    """Tests for circuit breaker integration in base handler."""

    def test_circuit_breaker_raises_on_open(self):
        """Test circuit breaker raises exception when open."""
        handler = UnifiedRetryHandler(
            max_retries=3,
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=1,
        )
        handler._circuit_breaker._state = CircuitState.OPEN

        with pytest.raises(Exception) as exc_info:
            handler.execute_with_retry(lambda: "success")

        assert "Circuit breaker is open" in str(exc_info.value)

    def test_circuit_breaker_records_success(self):
        """Test circuit breaker records success on successful call."""
        handler = UnifiedRetryHandler(
            max_retries=3,
            circuit_breaker_enabled=True,
        )
        result = handler.execute_with_retry(lambda: "success")
        assert result == "success"
        assert handler._circuit_breaker._failure_count == 0


class TestHealthTracking:
    """Tests for health tracking integration."""

    def test_health_tracker_records_success(self):
        """Test health tracker records successful operations."""
        mock_tracker = Mock()
        handler = UnifiedRetryHandler(
            max_retries=3,
            api_health_tracking=True,
        )
        handler._health_tracker = mock_tracker

        result = handler.execute_with_retry(lambda: "success")

        assert result == "success"
        mock_tracker.record_call.assert_called()
        call_args = mock_tracker.record_call.call_args
        assert call_args[1]["success"] is True

    def test_health_tracker_records_failure(self):
        """Test health tracker records failed operations."""
        mock_tracker = Mock()
        handler = UnifiedRetryHandler(
            max_retries=1,
            api_health_tracking=True,
        )
        handler._health_tracker = mock_tracker

        call_count = [0]

        def failing_func():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ConnectionError("connection failed")
            return "success"

        with pytest.raises(ConnectionError):
            handler.execute_with_retry(failing_func)

        mock_tracker.record_call.assert_called()


class TestContextAwareRetry:
    """Tests for context-aware retry logic."""

    def test_context_aware_file_content_404_not_retried(self):
        """Test that 404 errors for file content are not retried."""
        handler = UnifiedRetryHandler(
            max_retries=3,
            retry_on_404=True,
            context_aware_retry=True,
        )

        call_count = [0]

        def failing_func():
            call_count[0] += 1
            raise Exception("404 Not Found")

        with pytest.raises(Exception):
            handler.execute_with_retry(failing_func, context=OperationContext.FILE_CONTENT)

        assert call_count[0] == 1

    def test_context_aware_repo_access_401_not_retried(self):
        """Test that 401 errors for repository access are not retried."""
        handler = UnifiedRetryHandler(
            max_retries=3,
            retry_on_403=True,
            context_aware_retry=True,
        )

        call_count = [0]

        def failing_func():
            call_count[0] += 1
            raise Exception("401 Unauthorized")

        with pytest.raises(Exception):
            handler.execute_with_retry(failing_func, context=OperationContext.REPOSITORY_ACCESS)

        assert call_count[0] == 1

    def test_context_config_returns_context_specific(self):
        """Test that context config returns context-specific settings."""
        handler = UnifiedRetryHandler(
            max_retries=3,
            context_aware_retry=True,
        )

        config = handler._get_context_config(OperationContext.FILE_CONTENT)

        assert "max_retries" in config
        assert "retry_delay" in config


class TestLogging:
    """Tests for logging functionality."""

    def test_log_at_debug_level(self):
        """Test logging at DEBUG level."""
        handler = UnifiedRetryHandler(retry_log_level="DEBUG")
        with patch.object(handler, "_get_logger") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            handler._log_at_level("test message", "DEBUG")
            mock_logger.debug.assert_called_once_with("test message")

    def test_log_at_info_level(self):
        """Test logging at INFO level."""
        handler = UnifiedRetryHandler()
        with patch.object(handler, "_get_logger") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            handler._log_at_level("test message", "INFO")
            mock_logger.info.assert_called_once_with("test message")

    def test_log_at_warning_level(self):
        """Test logging at WARNING level."""
        handler = UnifiedRetryHandler()
        with patch.object(handler, "_get_logger") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            handler._log_at_level("test message", "WARNING")
            mock_logger.warning.assert_called_once_with("test message")

    def test_log_at_error_level(self):
        """Test logging at ERROR level."""
        handler = UnifiedRetryHandler()
        with patch.object(handler, "_get_logger") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            handler._log_at_level("test message", "ERROR")
            mock_logger.error.assert_called_once_with("test message")

    def test_log_at_critical_level(self):
        """Test logging at CRITICAL level."""
        handler = UnifiedRetryHandler()
        with patch.object(handler, "_get_logger") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            handler._log_at_level("test message", "CRITICAL")
            mock_logger.critical.assert_called_once_with("test message")

    def test_log_at_unknown_level_falls_back_to_info(self):
        """Test that unknown log levels fall back to INFO."""
        handler = UnifiedRetryHandler()
        with patch.object(handler, "_get_logger") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            handler._log_at_level("test message", "UNKNOWN")
            mock_logger.info.assert_called_once_with("test message")

    def test_log_permanent_failure(self):
        """Test logging of permanent failure."""
        handler = UnifiedRetryHandler()
        with patch.object(handler, "_get_logger") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            error = Exception("test error")
            handler._log_permanent_failure(error, should_retry=False, is_last_attempt=True)
            mock_logger.info.assert_called()

    def test_log_permanent_failure_custom_level(self):
        """Test permanent failure logging with custom level."""
        handler = UnifiedRetryHandler(permanent_failure_log_level="WARNING")
        with patch.object(handler, "_get_logger") as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            error = Exception("test error")
            handler._log_permanent_failure(error, should_retry=False, is_last_attempt=True)
            mock_logger.warning.assert_called()


class TestGetStats:
    """Tests for get_stats method."""

    def test_get_stats_basic(self):
        """Test basic stats without advanced features."""
        handler = UnifiedRetryHandler()

        stats = handler.get_stats()

        assert stats["circuit_breaker_enabled"] is False
        assert stats["adaptive_retry_enabled"] is False
        assert stats["api_health_tracking"] is False
        assert stats["context_aware_retry"] is False

    def test_get_stats_with_circuit_breaker(self):
        """Test stats with circuit breaker enabled."""
        handler = UnifiedRetryHandler(
            circuit_breaker_enabled=True,
        )

        stats = handler.get_stats()

        assert stats["circuit_breaker_enabled"] is True
        assert "circuit_breaker" in stats

    def test_get_stats_with_health_tracker(self):
        """Test stats with health tracking enabled."""
        mock_tracker = Mock()
        mock_tracker.get_stats.return_value = {"healthy": True}

        handler = UnifiedRetryHandler(api_health_tracking=True)
        handler._health_tracker = mock_tracker

        stats = handler.get_stats()

        assert stats["api_health_tracking"] is True
        assert "api_health" in stats


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_get_retry_handler(self):
        """Test get_retry_handler creates handler."""
        handler = get_retry_handler(max_retries=5)

        assert isinstance(handler, UnifiedRetryHandler)
        assert handler.max_retries == 5

    def test_get_retry_handler_honors_enabled_feature_flags(self):
        """Explicitly enabled feature flags build the breaker and health tracker."""
        handler = get_retry_handler(
            circuit_breaker_enabled=True,
            adaptive_retry_enabled=True,
            api_health_tracking=True,
            context_aware_retry=True,
        )

        assert handler.circuit_breaker_enabled is True
        assert handler.adaptive_retry_enabled is True
        assert handler._circuit_breaker is not None
        assert handler._health_tracker is not None
        assert handler._context_configs

    def test_get_retry_handler_honors_disabled_feature_flags(self):
        """Disabled feature flags are honored (no forced override)."""
        handler = get_retry_handler(
            circuit_breaker_enabled=False,
            adaptive_retry_enabled=False,
            api_health_tracking=False,
            context_aware_retry=False,
        )

        assert handler.circuit_breaker_enabled is False
        assert handler.adaptive_retry_enabled is False
        assert handler.api_health_tracking is False
        assert handler.context_aware_retry is False
        assert handler._circuit_breaker is None
        assert handler._health_tracker is None
        assert handler._context_configs == {}

    def test_unified_handler_honors_disabled_feature_flags(self):
        handler = UnifiedRetryHandler(circuit_breaker_enabled=False, api_health_tracking=False)

        assert handler._circuit_breaker is None
        assert handler._health_tracker is None


class TestLastExceptionFallback:
    """Tests for last exception fallback behavior."""

    def test_last_exception_raised_on_unexpected_state(self):
        """Test that last exception is raised if loop exits unexpectedly."""
        handler = UnifiedRetryHandler(max_retries=1)
        error = ConnectionError("connection error")

        with pytest.raises(ConnectionError) as exc_info:
            handler.execute_with_retry(lambda: (_ for _ in ()).throw(error))

        assert str(exc_info.value) == "connection error"


class TestRecordFailure:
    """Tests for _record_failure method."""

    def test_record_failure_with_circuit_breaker(self):
        """Test record failure updates circuit breaker."""
        handler = UnifiedRetryHandler(circuit_breaker_enabled=True)
        initial_failures = handler._circuit_breaker._failure_count

        handler._record_failure(Exception("test"))

        assert handler._circuit_breaker._failure_count == initial_failures + 1

    def test_record_failure_with_health_tracker(self):
        """Test record failure updates health tracker."""
        mock_tracker = Mock()
        handler = UnifiedRetryHandler(api_health_tracking=True)
        handler._health_tracker = mock_tracker

        handler._record_failure(Exception("test"))

        mock_tracker.record_call.assert_called()
        call_args = mock_tracker.record_call.call_args
        assert call_args[1]["success"] is False


class TestRecordSuccess:
    """Tests for _record_success method."""

    def test_record_success_with_circuit_breaker(self):
        """Test record success updates circuit breaker."""
        handler = UnifiedRetryHandler(circuit_breaker_enabled=True)
        handler._circuit_breaker._failure_count = 3

        handler._record_success(time.time())

        assert handler._circuit_breaker._failure_count == 0

    def test_record_success_with_health_tracker(self):
        """Test record success updates health tracker."""
        mock_tracker = Mock()
        handler = UnifiedRetryHandler(api_health_tracking=True)
        handler._health_tracker = mock_tracker

        handler._record_success(time.time())

        mock_tracker.record_call.assert_called()


class TestAsyncRetryHandler:
    """Tests for async retry functionality."""

    @pytest.mark.anyio
    async def test_async_circuit_breaker_raises_on_open(self):
        """Test async circuit breaker raises exception when open."""
        handler = UnifiedRetryHandler(
            max_retries=3,
            circuit_breaker_enabled=True,
        )
        handler._circuit_breaker._state = CircuitState.OPEN

        async def async_func():
            return "success"

        with pytest.raises(Exception) as exc_info:
            await handler.execute_with_retry_async(async_func)

        assert "Circuit breaker is open" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_async_health_tracker_records_success(self):
        """Test async health tracker records success."""
        mock_tracker = Mock()
        handler = UnifiedRetryHandler(api_health_tracking=True)
        handler._health_tracker = mock_tracker

        async def async_func():
            return "success"

        result = await handler.execute_with_retry_async(async_func)

        assert result == "success"
        mock_tracker.record_call.assert_called()

    @pytest.mark.anyio
    async def test_async_last_exception_fallback(self):
        """Test async raises last exception on unexpected state."""
        handler = UnifiedRetryHandler(max_retries=1)

        async def failing_func():
            raise ConnectionError("async connection error")

        with pytest.raises(ConnectionError) as exc_info:
            await handler.execute_with_retry_async(failing_func)

        assert "async connection error" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_async_uses_anyio_sleep(self):
        """Test that async retry uses anyio.sleep."""
        handler = UnifiedRetryHandler(max_retries=2, retry_delay=0.01)

        call_count = [0]

        async def transient_failure():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ConnectionError("connection error")
            return "success"

        with patch("prdiffer.infrastructure.utils.retry.handler.anyio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            result = await handler.execute_with_retry_async(transient_failure)
            assert result == "success"
            mock_sleep.assert_called()


class TestShouldRetryError:
    """Tests for _should_retry_error method."""

    def test_should_retry_uses_error_classifier(self):
        """Test that _should_retry_error uses error classifier."""
        handler = UnifiedRetryHandler(retry_on_403=True)

        error = Exception("403 rate limit exceeded")
        should_retry = handler._should_retry_error(error)

        assert should_retry is True

    def test_should_not_retry_classification_says_no(self):
        """Test that non-retryable errors are not retried."""
        handler = UnifiedRetryHandler(retry_on_404=False)

        error = Exception("404 not found")
        should_retry = handler._should_retry_error(error)

        assert should_retry is False


class TestCalculateRetryDelay:
    """Tests for _calculate_retry_delay method."""

    def test_basic_delay_calculation(self):
        """Test basic delay calculation."""
        handler = UnifiedRetryHandler(retry_delay=1.0)

        rate_limit_info = RateLimitInfo(
            remaining=100,
            limit=5000,
            reset_at=time.time() + 3600,
            retry_after=None,
        )
        delay = handler._calculate_retry_delay(
            attempt=0,
            error=Exception("test"),
            base_delay=1.0,
            backoff_multiplier=2.0,
            use_adaptive=False,
            rate_limit_info=rate_limit_info,
            is_secondary_rate_limit=False,
        )

        assert delay >= 0

    def test_adaptive_delay_with_health_tracker(self):
        """Test adaptive delay with health tracker."""
        mock_tracker = Mock()
        mock_tracker.get_suggested_delay.return_value = 5.0

        handler = UnifiedRetryHandler(
            adaptive_retry_enabled=True,
            api_health_tracking=True,
        )
        handler._health_tracker = mock_tracker

        rate_limit_info = RateLimitInfo(
            remaining=100,
            limit=5000,
            reset_at=int(time.time() + 3600),
            retry_after=None,
        )
        delay = handler._calculate_retry_delay(
            attempt=0,
            error=Exception("test"),
            base_delay=1.0,
            backoff_multiplier=2.0,
            use_adaptive=True,
            rate_limit_info=rate_limit_info,
            is_secondary_rate_limit=False,
        )

        assert delay >= 0


class TestLogRetryAttempt:
    """Tests for log_retry_attempt standalone function."""

    def test_log_retry_attempt_basic(self):
        """Test basic retry attempt logging."""
        from prdiffer.infrastructure.utils.retry_logger import log_retry_attempt

        mock_logger = Mock()

        rate_limit_info = RateLimitInfo(
            remaining=100,
            limit=5000,
            reset_at=int(time.time() + 3600),
            retry_after=None,
        )
        log_retry_attempt(
            logger=mock_logger,
            attempt=0,
            delay=1.0,
            error=Exception("test error"),
            retry_log_level="DEBUG",
            context=OperationContext.FILE_CONTENT.value,
            rate_limit_info=rate_limit_info,
            is_secondary_rate_limit=False,
        )

        mock_logger.debug.assert_called()
