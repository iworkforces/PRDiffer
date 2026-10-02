"""Unit tests for CircuitBreaker utility component.

Tests the circuit breaker pattern implementation with state transitions,
failure handling, and recovery mechanisms.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from unittest.mock import Mock
import pytest

from prdiffer.infrastructure.utils.circuit_breaker_core import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerOpenException,
)
import prdiffer.infrastructure.utils.circuit_breaker_core as core


@pytest.mark.unit
class TestCircuitBreakerInitialization:
    """Test suite for CircuitBreaker initialization."""

    def test_initialization_with_defaults(self):
        """Test circuit breaker initialization with default values."""
        breaker = CircuitBreaker()

        assert breaker.failure_threshold == 5
        assert breaker.timeout == 60.0
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0
        assert breaker.get_stats()["last_failure_time"] is None
        assert breaker.get_stats()["successful_calls"] == 0

    def test_initialization_with_custom_values(self):
        """Test circuit breaker initialization with custom values."""
        breaker = CircuitBreaker(failure_threshold=10, timeout=120.0)

        assert breaker.failure_threshold == 10
        assert breaker.timeout == 120.0
        assert breaker.state == CircuitState.CLOSED

    def test_initialization_with_logger(self):
        """Test circuit breaker initialization with custom logger."""
        mock_logger = Mock()
        breaker = CircuitBreaker(logger=mock_logger)

        assert breaker._logger == mock_logger


@pytest.mark.unit
class TestCircuitBreakerStateTransitions:
    """Test suite for circuit breaker state transitions."""

    def test_closed_to_open_on_threshold_reached(self):
        """Test transition from CLOSED to OPEN when failure threshold is reached."""
        breaker = CircuitBreaker(failure_threshold=3, timeout=60.0)

        # Record failures up to threshold
        assert breaker.state == CircuitState.CLOSED
        breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED
        breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED

        # Third failure should open the circuit
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3

    def test_open_to_half_open_after_timeout(self):
        """Test transition from OPEN to HALF_OPEN after timeout."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=0.1)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # Wait for timeout to elapse
        time.sleep(0.15)

        # can_execute should transition to half-open and return True
        assert breaker.can_execute() is True
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_to_closed_on_success(self):
        """Test transition from HALF_OPEN to CLOSED on successful operation."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()

        # Wait for timeout
        breaker._last_failure_time = time.time() - breaker.timeout - 1
        assert breaker.can_execute() is True
        assert breaker.state == CircuitState.HALF_OPEN

        # Record success - should close the circuit
        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_half_open_to_open_on_failure(self):
        """Test transition from HALF_OPEN back to OPEN on failure."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()

        # Wait for timeout
        breaker._last_failure_time = time.time() - breaker.timeout - 1
        assert breaker.can_execute() is True
        assert breaker.state == CircuitState.HALF_OPEN

        # Record failure - should open again
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3

    def test_closed_resets_failure_count_on_success(self):
        """Test that success in CLOSED state resets failure count."""
        breaker = CircuitBreaker(failure_threshold=3)

        # Record some failures
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.failure_count == 2

        # Record success should reset failure count
        breaker.record_success()
        assert breaker.failure_count == 0
        assert breaker.state == CircuitState.CLOSED


@pytest.mark.unit
class TestCircuitBreakerCanExecute:
    """Test suite for can_execute method."""

    def test_can_execute_when_closed(self):
        """Test can_execute returns True when circuit is CLOSED."""
        breaker = CircuitBreaker()

        assert breaker.can_execute() is True
        assert breaker.state == CircuitState.CLOSED

    def test_can_execute_when_open_within_timeout(self):
        """Test can_execute returns False when circuit is OPEN and timeout hasn't elapsed."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()

        assert breaker.can_execute() is False
        assert breaker.state == CircuitState.OPEN

    def test_can_execute_when_open_after_timeout(self):
        """Test can_execute returns True when circuit is OPEN and timeout has elapsed."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=0.1)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.can_execute() is False

        # Wait for timeout
        time.sleep(0.15)

        # Should now be able to execute (transitions to HALF_OPEN)
        assert breaker.can_execute() is True

    def test_can_execute_when_half_open(self):
        """Test can_execute returns True when circuit is HALF_OPEN."""
        # Use longer timeout to avoid race condition
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open the circuit and wait for timeout
        breaker.record_failure()
        breaker.record_failure()

        # Manually set last failure time to trigger HALF_OPEN transition
        breaker._last_failure_time = time.time() - breaker.timeout - 1

        # Trigger transition to HALF_OPEN and verify
        assert breaker.can_execute() is True
        assert breaker.state == CircuitState.HALF_OPEN


@pytest.mark.unit
class TestCircuitBreakerThreadSafety:
    """Test suite for concurrent use of CircuitBreaker from worker threads."""

    @pytest.mark.thread_safety
    def test_concurrent_threads_share_failure_and_recovery(self, monkeypatch):
        clock = [100.0]
        monkeypatch.setattr(core, "time", SimpleNamespace(time=lambda: clock[0]))
        breaker = CircuitBreaker(failure_threshold=12, timeout=10)
        barrier = threading.Barrier(3)

        def worker() -> None:
            barrier.wait(timeout=2)
            for _ in range(4):
                breaker.record_failure()

        with ThreadPoolExecutor(max_workers=3) as workers:
            futures = [workers.submit(worker) for _ in range(3)]
            for future in futures:
                future.result(timeout=3)

        assert breaker.get_stats()["failure_count"] == 12
        assert breaker.state == CircuitState.OPEN
        assert not breaker.can_execute()
        clock[0] = 110.0
        assert breaker.can_execute()
        assert breaker.state == CircuitState.HALF_OPEN
        breaker.record_failure()
        assert not breaker.can_execute()
        clock[0] = 120.0
        assert breaker.can_execute()
        breaker.record_success()
        assert breaker.get_stats()["state"] == CircuitState.CLOSED.value
        assert breaker.failure_count == 0

    def test_logger_callback_can_inspect_breaker_during_transition(self):
        logger = Mock()
        breaker = CircuitBreaker(failure_threshold=1, logger=logger)
        logger.warning.side_effect = lambda message: breaker.get_stats()

        breaker.record_failure()

        assert breaker.state == CircuitState.OPEN
        logger.warning.assert_called_once()


@pytest.mark.unit
class TestCircuitBreakerStatistics:
    """Test suite for circuit breaker statistics."""

    def test_get_stats(self):
        """Test get_stats returns correct statistics."""
        breaker = CircuitBreaker(failure_threshold=5, timeout=120.0)

        # Record some state
        breaker.record_failure()
        breaker.record_failure()

        stats = breaker.get_stats()

        assert stats["state"] == CircuitState.CLOSED.value
        assert stats["failure_count"] == 2
        assert stats["failure_threshold"] == 5
        assert stats["timeout"] == 120.0
        assert stats["successful_calls"] == 0
        assert stats["last_failure_time"] is not None

    def test_get_stats_after_opening(self):
        """Test get_stats reflects OPEN state."""
        breaker = CircuitBreaker(failure_threshold=2)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()

        stats = breaker.get_stats()

        assert stats["state"] == CircuitState.OPEN.value
        assert stats["failure_count"] == 2


@pytest.mark.unit
class TestCircuitBreakerException:
    """Test suite for CircuitBreakerOpenException."""

    def test_exception_creation(self):
        """Test CircuitBreakerOpenException can be created."""
        exc = CircuitBreakerOpenException()
        assert exc.message == "Circuit breaker is open"

    def test_exception_with_custom_message(self):
        """Test CircuitBreakerOpenException with custom message."""
        exc = CircuitBreakerOpenException("Custom message")
        assert exc.message == "Custom message"


@pytest.mark.unit
class TestCircuitBreakerEdgeCases:
    """Test suite for circuit breaker edge cases."""

    def test_zero_failure_threshold(self):
        """Test circuit breaker with zero failure threshold."""
        breaker = CircuitBreaker(failure_threshold=0, timeout=60.0)

        # Should open immediately on first failure
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 1

    def test_very_long_timeout(self):
        """Test circuit breaker with very long timeout."""
        breaker = CircuitBreaker(failure_threshold=3, timeout=999999.0)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()

        assert breaker.state == CircuitState.OPEN

        # Can execute should still be False since timeout hasn't elapsed
        assert breaker.can_execute() is False

    def test_concurrent_operations(self):
        """Test circuit breaker is thread-safe for concurrent operations."""
        import threading

        breaker = CircuitBreaker(failure_threshold=10, timeout=60.0)
        results = []
        errors = []

        def worker():
            try:
                # Simulate concurrent operations
                for _ in range(5):
                    if breaker.can_execute():
                        breaker.record_failure()
                    results.append(breaker.state.value)
            except Exception as e:
                errors.append(e)

        # Create multiple threads
        threads = [threading.Thread(target=worker) for _ in range(3)]

        # Start all threads
        for t in threads:
            t.start()

        # Wait for completion
        for t in threads:
            t.join(timeout=5)

        # Should have no errors
        assert len(errors) == 0
        assert len(results) == 15  # 3 threads * 5 operations each

    def test_statistics_consistency(self):
        """Test statistics remain consistent across state transitions."""
        breaker = CircuitBreaker(failure_threshold=3)

        # Get initial stats
        initial_stats = breaker.get_stats()
        assert initial_stats["failure_count"] == 0

        # Record failure
        breaker.record_failure()
        stats_after_one = breaker.get_stats()
        assert stats_after_one["failure_count"] == 1

        # Record another failure
        breaker.record_failure()
        stats_after_two = breaker.get_stats()
        assert stats_after_two["failure_count"] == 2

        # Record success should reset count
        breaker.record_success()
        stats_after_success = breaker.get_stats()
        assert stats_after_success["failure_count"] == 0


@pytest.mark.unit
class TestCircuitBreakerRecovery:
    """Test suite for circuit breaker recovery scenarios."""

    def test_recovery_from_open_state(self):
        """Test circuit breaker recovery after opening."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=0.1)

        # Open the circuit
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # Wait for timeout and trigger transition
        time.sleep(0.15)
        breaker.can_execute()

        # Should be in HALF_OPEN state
        assert breaker.state == CircuitState.HALF_OPEN

        # Record success to close
        breaker.record_success()

        # Should be back to CLOSED
        assert breaker.state == CircuitState.CLOSED
        assert breaker.can_execute() is True

    def test_recovery_after_multiple_failures(self):
        """Test circuit breaker recovery after multiple failure cycles."""
        breaker = CircuitBreaker(failure_threshold=3, timeout=0.1)

        # Cycle 1: Open circuit
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # Recover
        time.sleep(0.15)
        breaker.can_execute()
        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED

        # Cycle 2: Open circuit again
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # Recover again
        time.sleep(0.15)
        breaker.can_execute()
        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED

    def test_half_open_failure_recovery(self):
        """Test recovery when HALF_OPEN state fails again."""
        breaker = CircuitBreaker(failure_threshold=3, timeout=0.1)

        # Open circuit and transition to HALF_OPEN
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        time.sleep(0.15)
        breaker.can_execute()
        assert breaker.state == CircuitState.HALF_OPEN

        # Failure in HALF_OPEN should open circuit
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # Recover
        time.sleep(0.15)
        breaker.can_execute()
        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED


@pytest.mark.unit
class TestCircuitBreakerFactoryFunctions:
    """Test suite for circuit breaker direct instantiation."""

    def test_direct_instantiation_with_custom_values(self):
        """Test direct CircuitBreaker instantiation with custom values."""
        breaker = CircuitBreaker(failure_threshold=7, timeout=45.0)

        assert breaker.failure_threshold == 7
        assert breaker.timeout == 45.0
        assert breaker.state == CircuitState.CLOSED
