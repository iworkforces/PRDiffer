"""Unit tests for Retry Handler.

This module contains comprehensive tests for the UnifiedRetryHandler class,
covering retry logic, circuit breaker integration, and error classification.
"""

import pytest
from unittest.mock import patch

from prdiffer.infrastructure.utils.retry.handler import (
    UnifiedRetryHandler,
)
from prdiffer.infrastructure.utils.retry.models import (
    RETRY_EXCEPTIONS,
)


class TestUnifiedRetryHandler:
    """Test suite for UnifiedRetryHandler."""

    @pytest.fixture
    def retry_handler(self):
        """Create UnifiedRetryHandler instance for testing."""
        return UnifiedRetryHandler(
            max_retries=3,
            retry_delay=0.1,  # Short delay for testing
        )

    def test_initialization(self, retry_handler):
        """Test handler initialization with default parameters."""
        assert retry_handler.max_retries == 3
        assert retry_handler.retry_delay == 0.1
        assert retry_handler.retry_on_404 is False
        assert retry_handler.retry_on_403 is True
        assert retry_handler.retry_on_500 is True

    def test_successful_execution_no_retry(self, retry_handler):
        """Test that successful function is not retried."""
        call_count = [0]

        def successful_func():
            call_count[0] += 1
            return "success"

        result = retry_handler.execute_with_retry(successful_func)

        assert result == "success"
        assert call_count[0] == 1  # Called only once

    def test_retry_on_connection_error(self, retry_handler):
        """Test that connection errors with 'connection' in message trigger retry."""
        call_count = [0]

        def transient_func():
            call_count[0] += 1
            # Fail on first 2 calls, succeed on 3rd call (3 total attempts with max_retries=3)
            if call_count[0] < 3:
                raise ConnectionError("Connection failed - transient error")
            return "success"

        result = retry_handler.execute_with_retry(transient_func)

        assert result == "success"
        assert call_count[0] == 3  # 3 total attempts

    def test_no_retry_on_non_transient_error(self, retry_handler):
        """Test that non-transient errors (without retry keywords) are not retried."""
        call_count = [0]

        def non_transient_func():
            call_count[0] += 1
            # Error message doesn't contain retry keywords (timeout, connection, network, etc.)
            raise ValueError("Some random error")

        with pytest.raises(ValueError, match="Some random error"):
            retry_handler.execute_with_retry(non_transient_func)

        # Should only be called once since error doesn't match retry criteria
        assert call_count[0] == 1


class TestRetryHandlerCircuitBreaker:
    """Test suite for circuit breaker integration."""

    @pytest.fixture
    def retry_handler_with_circuit_breaker(self):
        """Create UnifiedRetryHandler with circuit breaker enabled."""
        return UnifiedRetryHandler(
            max_retries=3,
            retry_delay=0.1,
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=2,
            circuit_breaker_timeout=1.0,
        )

    def test_circuit_breaker_opens_on_threshold(self, retry_handler_with_circuit_breaker):
        """Test that circuit breaker opens after threshold failures."""
        call_count = [0]

        def failing_func():
            call_count[0] += 1
            # Use "connection" in error message so it will be retried
            raise ConnectionError("Connection failed - always fails")

        # Execute until circuit breaker opens (after threshold failures)
        # First call: initial attempt + retries (total max_retries attempts)
        # Each attempt calls failing_func which records the failure

        # First execution - should trigger retries and record failures
        with pytest.raises(ConnectionError):
            retry_handler_with_circuit_breaker.execute_with_retry(failing_func)

        # Circuit breaker should be open now after reaching failure threshold
        # Check the state using the enum value
        from prdiffer.infrastructure.utils.circuit_breaker_core import CircuitState

        assert retry_handler_with_circuit_breaker._circuit_breaker.state == CircuitState.OPEN


class TestRetryHandlerErrorClassification:
    """Test suite for error classification and retry decisions."""

    @pytest.fixture
    def retry_handler(self):
        """Create UnifiedRetryHandler instance for testing."""
        return UnifiedRetryHandler()

    def test_should_retry_connection_error(self, retry_handler):
        """Test that connection errors with 'connection' in message are retried."""
        error = ConnectionError("Connection failed")
        should_retry = retry_handler._should_retry_error(error, None)
        assert should_retry is True

    def test_should_retry_timeout_error(self, retry_handler):
        """Test that timeout errors are retried."""
        # Use "timeout" (single word) to match the retry logic check
        error = TimeoutError("Connection timeout")
        should_retry = retry_handler._should_retry_error(error, None)
        # Contains "timeout" so should be retried
        assert should_retry is True

    def test_should_not_retry_system_exceptions(self, retry_handler):
        """Test that system exceptions are NOT caught for retry."""
        # These exceptions should NOT be in RETRY_EXCEPTIONS
        assert KeyboardInterrupt not in RETRY_EXCEPTIONS
        assert SystemExit not in RETRY_EXCEPTIONS
        assert GeneratorExit not in RETRY_EXCEPTIONS


@pytest.mark.asyncio
class TestRetryHandlerAsync:
    """Test suite for async retry functionality."""

    @pytest.fixture
    def retry_handler(self):
        """Create UnifiedRetryHandler instance for testing."""
        return UnifiedRetryHandler(
            max_retries=3,  # Total attempts = 3 (not initial + retries)
            retry_delay=0.1,
        )

    async def test_async_retry_on_transient_failure(self, retry_handler):
        """Test async retry on transient failures with 'connection' in message."""
        call_count = [0]

        async def transient_async_func():
            call_count[0] += 1
            # Fail on first 2 calls, succeed on 3rd call (3 total attempts)
            if call_count[0] < 3:
                # Use "connection" in error message so it will be retried
                raise ConnectionError("Connection failed - transient error")
            return "async_success"

        result = await retry_handler.execute_with_retry_async(transient_async_func)

        assert result == "async_success"
        assert call_count[0] == 3  # 3 total attempts

    async def test_async_uses_anyio_sleep(self, retry_handler):
        """Test that async retry uses anyio.sleep instead of time.sleep."""
        executed_sleeps = []

        async def mock_sleep(duration):
            executed_sleeps.append(duration)

        async def failing_func():
            # Use "connection" in error message so it will be retried
            raise ConnectionError("Connection failed")

        with patch("prdiffer.infrastructure.utils.retry.handler.anyio.sleep", mock_sleep):
            with pytest.raises(ConnectionError):
                await retry_handler.execute_with_retry_async(failing_func)

        # Should have called sleep (number of retries)
        assert len(executed_sleeps) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
