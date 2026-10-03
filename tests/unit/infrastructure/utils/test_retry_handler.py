"""Unit tests for Retry Handler.

This module contains comprehensive tests for the UnifiedRetryHandler class,
covering retry logic, circuit breaker integration, and error classification.
"""

import pytest
import anyio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from prdiffer.infrastructure.utils.retry.handler import (
    UnifiedRetryHandler,
)
from prdiffer.infrastructure.utils.retry.models import (
    RETRY_EXCEPTIONS,
)
from prdiffer.infrastructure.utils.circuit_breaker_core import CircuitBreakerOpenException
import prdiffer.infrastructure.utils.circuit_breaker_core as core


def recovering_handler(monkeypatch, health):
    handler = UnifiedRetryHandler(max_retries=1, circuit_breaker_enabled=True,
                                 circuit_breaker_failure_threshold=1, circuit_breaker_timeout=10,
                                 api_health_tracking=health)
    clock = [100.0]
    monkeypatch.setattr(core, "time", SimpleNamespace(time=lambda: clock[0]))

    def fail():
        raise ConnectionError("connection")

    with pytest.raises(ConnectionError):
        handler.execute_with_retry(fail)
    clock[0] = 110
    return handler


class TestBreakerRecoveryRegression:
    @pytest.mark.parametrize("health", [False, True])
    def test_thread_trial_rejects_contender_before_backend(self, monkeypatch, health):
        handler = recovering_handler(monkeypatch, health)
        entered, finish = threading.Event(), threading.Event()
        loser_calls = []

        def trial():
            entered.set()
            assert finish.wait(2)
            return "winner"

        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(handler.execute_with_retry, trial)
            assert entered.wait(2)
            try:
                with pytest.raises(CircuitBreakerOpenException):
                    pool.submit(handler.execute_with_retry, lambda: loser_calls.append(1)).result(2)
                assert loser_calls == []
            finally:
                finish.set()
            assert winner.result(2) == "winner"
        assert handler._circuit_breaker.state.value == "closed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("health", [False, True])
    async def test_async_trial_rejects_async_and_thread_contenders(self, monkeypatch, health):
        handler = recovering_handler(monkeypatch, health)
        entered, finish = anyio.Event(), anyio.Event()
        results = []
        loser_calls = []

        async def trial():
            entered.set()
            await finish.wait()
            return "winner"

        async def run_trial():
            results.append(await handler.execute_with_retry_async(trial))

        async def loser():
            loser_calls.append(1)

        with anyio.fail_after(3):
            async with anyio.create_task_group() as group:
                group.start_soon(run_trial)
                await entered.wait()
                with pytest.raises(CircuitBreakerOpenException):
                    await handler.execute_with_retry_async(loser)
                with pytest.raises(CircuitBreakerOpenException):
                    await anyio.to_thread.run_sync(lambda: handler.execute_with_retry(lambda: loser_calls.append(1)))
                assert loser_calls == []
                finish.set()
        assert results == ["winner"]
        assert handler._circuit_breaker.state.value == "closed"

    @pytest.mark.parametrize("error", [ValueError("unknown"), KeyboardInterrupt(), OSError("transport")])
    def test_neutral_and_abnormal_trial_exit(self, monkeypatch, error):
        handler = recovering_handler(monkeypatch, False)

        def fail():
            raise error

        with pytest.raises(type(error)):
            handler.execute_with_retry(fail)
        if isinstance(error, OSError):
            assert handler._circuit_breaker.state.value == "open"
            assert handler._circuit_breaker.failure_count == 2
        else:
            assert handler._circuit_breaker.state.value == "half_open"
            assert handler._circuit_breaker.failure_count == 1
            assert handler.execute_with_retry(lambda: "replacement") == "replacement"

    @pytest.mark.asyncio
    async def test_cancelled_trial_releases_without_closing(self, monkeypatch):
        handler = recovering_handler(monkeypatch, False)
        entered = anyio.Event()

        async def trial():
            entered.set()
            await anyio.sleep_forever()

        async def run_trial():
            await handler.execute_with_retry_async(trial)

        with anyio.fail_after(3):
            async with anyio.create_task_group() as group:
                group.start_soon(run_trial)
                await entered.wait()
                group.cancel_scope.cancel()
        assert handler._circuit_breaker.state.value == "half_open"
        assert handler._circuit_breaker.failure_count == 1
        assert handler.execute_with_retry(lambda: "replacement") == "replacement"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403, 404, 422])
    async def test_excluded_async_trial_allows_replacement(self, monkeypatch, status):
        from github import GithubException
        handler = recovering_handler(monkeypatch, True)

        async def fail():
            raise GithubException(status, {"message": "excluded"})

        with pytest.raises(GithubException):
            await handler.execute_with_retry_async(fail)
        assert handler._circuit_breaker.state.value == "half_open"
        assert handler._circuit_breaker.failure_count == 1

        async def replacement():
            return "replacement"

        assert await handler.execute_with_retry_async(replacement) == "replacement"
        assert handler._circuit_breaker.state.value == "closed"

    @pytest.mark.parametrize("health", [False, True])
    def test_terminal_failure_count_and_recovery(self, monkeypatch, health):
        handler = UnifiedRetryHandler(max_retries=3, retry_delay=0, circuit_breaker_enabled=True,
                                     circuit_breaker_failure_threshold=1, circuit_breaker_timeout=10,
                                     api_health_tracking=health)
        clock = [100.0]
        monkeypatch.setattr("prdiffer.infrastructure.utils.circuit_breaker_core.time.time", lambda: clock[0])
        monkeypatch.setattr(handler, "_calculate_retry_delay", lambda *args, **kwargs: 0)
        calls = []

        def fail():
            calls.append(1)
            raise ConnectionError("connection failed")

        with pytest.raises(ConnectionError):
            handler.execute_with_retry(fail)
        assert len(calls) == 3
        assert handler._circuit_breaker.failure_count == 1
        clock[0] = 110
        assert handler.execute_with_retry(lambda: "recovered") == "recovered"
        assert handler._circuit_breaker.state.value == "closed"

    @pytest.mark.parametrize("status,message,count", [(401, "auth", 0), (404, "missing", 0),
        (422, "invalid", 0), (403, "permission", 0), (403, "API abuse", 1),
        (429, "limited", 1), (501, "server", 1), (503, "server", 1)])
    def test_terminal_github_classification(self, status, message, count):
        from github import GithubException
        handler = UnifiedRetryHandler(max_retries=1, circuit_breaker_enabled=True)
        error = GithubException(status, {"message": message})

        def fail():
            raise error

        with pytest.raises(GithubException):
            handler.execute_with_retry(fail)
        assert handler._circuit_breaker.failure_count == count


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

        from prdiffer.infrastructure.utils.circuit_breaker_core import CircuitState

        assert retry_handler_with_circuit_breaker._circuit_breaker.state == CircuitState.CLOSED
        assert retry_handler_with_circuit_breaker._circuit_breaker.failure_count == 1
        with pytest.raises(ConnectionError):
            retry_handler_with_circuit_breaker.execute_with_retry(failing_func)
        assert call_count[0] == 6
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
