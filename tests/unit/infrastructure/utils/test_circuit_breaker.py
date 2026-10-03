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


def fail_call(breaker: CircuitBreaker) -> None:
    permit = breaker.acquire()
    assert permit is not None
    breaker.record_failure(permit)


def succeed_call(breaker: CircuitBreaker) -> None:
    permit = breaker.acquire()
    assert permit is not None
    breaker.record_success(permit)


def admission_available(breaker: CircuitBreaker) -> bool:
    permit = breaker.acquire()
    if permit is None:
        return False
    breaker.release(permit)
    return True


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


class TestPermitFencing:
    def test_stale_and_duplicate_outcomes(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr(core, "time", SimpleNamespace(time=lambda: clock[0]))
        breaker = CircuitBreaker(failure_threshold=1, timeout=10)
        old = breaker.acquire()
        opening = breaker.acquire()
        assert old is not None and opening is not None
        breaker.record_failure(opening)
        clock[0] = 10
        trial = breaker.acquire()
        assert trial is not None
        assert breaker.acquire() is None
        breaker.record_success(old)
        assert breaker.state == CircuitState.HALF_OPEN
        breaker.record_failure(trial)
        breaker.record_failure(trial)
        assert breaker.failure_count == 2
        assert breaker.acquire() is None
        clock[0] = 20
        newer = breaker.acquire()
        assert newer is not None
        breaker.record_success(trial)
        breaker.release(trial)
        assert breaker.acquire() is None
        assert breaker.state == CircuitState.HALF_OPEN
        breaker.release(newer)
        replacement = breaker.acquire()
        assert replacement is not None
        breaker.record_success(newer)
        assert breaker.state == CircuitState.HALF_OPEN
        breaker.record_success(replacement)
        breaker.record_failure(replacement)
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_foreign_permit_is_ignored(self):
        first, second = CircuitBreaker(), CircuitBreaker()
        permit = first.acquire()
        assert permit is not None
        second.record_failure(permit)
        assert second.failure_count == 0
        first.record_failure(permit)
        assert first.failure_count == 1


@pytest.mark.unit
class TestCircuitBreakerStateTransitions:
    """Test suite for circuit breaker state transitions."""

    def test_closed_to_open_on_threshold_reached(self):
        """Test transition from CLOSED to OPEN when failure threshold is reached."""
        breaker = CircuitBreaker(failure_threshold=3, timeout=60.0)

        # Record failures up to threshold
        assert breaker.state == CircuitState.CLOSED
        fail_call(breaker)
        assert breaker.state == CircuitState.CLOSED
        fail_call(breaker)
        assert breaker.state == CircuitState.CLOSED

        # Third failure should open the circuit
        fail_call(breaker)
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3

    def test_open_to_half_open_after_timeout(self):
        """Test transition from OPEN to HALF_OPEN after timeout."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=0.1)

        # Open the circuit
        fail_call(breaker)
        fail_call(breaker)
        assert breaker.state == CircuitState.OPEN

        # Wait for timeout to elapse
        time.sleep(0.15)

        # can_execute should transition to half-open and return True
        assert admission_available(breaker) is True
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_to_closed_on_success(self):
        """Test transition from HALF_OPEN to CLOSED on successful operation."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open the circuit
        fail_call(breaker)
        fail_call(breaker)

        # Wait for timeout
        breaker._last_failure_time = time.time() - breaker.timeout - 1
        assert admission_available(breaker) is True
        assert breaker.state == CircuitState.HALF_OPEN

        # Record success - should close the circuit
        succeed_call(breaker)
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_half_open_to_open_on_failure(self):
        """Test transition from HALF_OPEN back to OPEN on failure."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open the circuit
        fail_call(breaker)
        fail_call(breaker)

        # Wait for timeout
        breaker._last_failure_time = time.time() - breaker.timeout - 1
        assert admission_available(breaker) is True
        assert breaker.state == CircuitState.HALF_OPEN

        # Record failure - should open again
        fail_call(breaker)
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3

    def test_closed_resets_failure_count_on_success(self):
        """Test that success in CLOSED state resets failure count."""
        breaker = CircuitBreaker(failure_threshold=3)

        # Record some failures
        fail_call(breaker)
        fail_call(breaker)
        assert breaker.failure_count == 2

        # Record success should reset failure count
        succeed_call(breaker)
        assert breaker.failure_count == 0
        assert breaker.state == CircuitState.CLOSED


@pytest.mark.unit
class TestCircuitBreakerCanExecute:
    """Test suite for can_execute method."""

    def test_can_execute_when_closed(self):
        """Test can_execute returns True when circuit is CLOSED."""
        breaker = CircuitBreaker()

        assert admission_available(breaker) is True
        assert breaker.state == CircuitState.CLOSED

    def test_can_execute_when_open_within_timeout(self):
        """Test can_execute returns False when circuit is OPEN and timeout hasn't elapsed."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open the circuit
        fail_call(breaker)
        fail_call(breaker)

        assert admission_available(breaker) is False
        assert breaker.state == CircuitState.OPEN

    def test_can_execute_when_open_after_timeout(self):
        """Test can_execute returns True when circuit is OPEN and timeout has elapsed."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=0.1)

        # Open the circuit
        fail_call(breaker)
        fail_call(breaker)
        assert admission_available(breaker) is False

        # Wait for timeout
        time.sleep(0.15)

        # Should now be able to execute (transitions to HALF_OPEN)
        assert admission_available(breaker) is True

    def test_can_execute_when_half_open(self):
        """Test can_execute returns True when circuit is HALF_OPEN."""
        # Use longer timeout to avoid race condition
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open the circuit and wait for timeout
        fail_call(breaker)
        fail_call(breaker)

        # Manually set last failure time to trigger HALF_OPEN transition
        breaker._last_failure_time = time.time() - breaker.timeout - 1

        # Trigger transition to HALF_OPEN and verify
        assert admission_available(breaker) is True
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
                fail_call(breaker)

        with ThreadPoolExecutor(max_workers=3) as workers:
            futures = [workers.submit(worker) for _ in range(3)]
            for future in futures:
                future.result(timeout=3)

        assert breaker.get_stats()["failure_count"] == 12
        assert breaker.state == CircuitState.OPEN
        assert not admission_available(breaker)
        clock[0] = 110.0
        assert admission_available(breaker)
        assert breaker.state == CircuitState.HALF_OPEN
        fail_call(breaker)
        assert not admission_available(breaker)
        clock[0] = 120.0
        assert admission_available(breaker)
        succeed_call(breaker)
        assert breaker.get_stats()["state"] == CircuitState.CLOSED.value
        assert breaker.failure_count == 0

    def test_logger_callback_can_inspect_breaker_during_transition(self):
        logger = Mock()
        breaker = CircuitBreaker(failure_threshold=1, logger=logger)
        logger.warning.side_effect = lambda message: breaker.get_stats()

        fail_call(breaker)

        assert breaker.state == CircuitState.OPEN
        logger.warning.assert_called_once()


@pytest.mark.unit
class TestCircuitBreakerStatistics:
    """Test suite for circuit breaker statistics."""

    def test_get_stats(self):
        """Test get_stats returns correct statistics."""
        breaker = CircuitBreaker(failure_threshold=5, timeout=120.0)

        # Record some state
        fail_call(breaker)
        fail_call(breaker)

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
        fail_call(breaker)
        fail_call(breaker)

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
        fail_call(breaker)
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 1

    def test_very_long_timeout(self):
        """Test circuit breaker with very long timeout."""
        breaker = CircuitBreaker(failure_threshold=3, timeout=999999.0)

        # Open the circuit
        fail_call(breaker)
        fail_call(breaker)
        fail_call(breaker)

        assert breaker.state == CircuitState.OPEN

        # Can execute should still be False since timeout hasn't elapsed
        assert admission_available(breaker) is False

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
                    permit = breaker.acquire()
                    if permit is not None:
                        breaker.record_failure(permit)
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
        fail_call(breaker)
        stats_after_one = breaker.get_stats()
        assert stats_after_one["failure_count"] == 1

        # Record another failure
        fail_call(breaker)
        stats_after_two = breaker.get_stats()
        assert stats_after_two["failure_count"] == 2

        # Record success should reset count
        succeed_call(breaker)
        stats_after_success = breaker.get_stats()
        assert stats_after_success["failure_count"] == 0


@pytest.mark.unit
class TestCircuitBreakerRecovery:
    """Test suite for circuit breaker recovery scenarios."""

    def test_recovery_from_open_state(self):
        """Test circuit breaker recovery after opening."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=0.1)

        # Open the circuit
        fail_call(breaker)
        fail_call(breaker)
        assert breaker.state == CircuitState.OPEN

        # Wait for timeout and trigger transition
        time.sleep(0.15)
        admission_available(breaker)

        # Should be in HALF_OPEN state
        assert breaker.state == CircuitState.HALF_OPEN

        # Record success to close
        succeed_call(breaker)

        # Should be back to CLOSED
        assert breaker.state == CircuitState.CLOSED
        assert admission_available(breaker) is True

    def test_recovery_after_multiple_failures(self):
        """Test circuit breaker recovery after multiple failure cycles."""
        breaker = CircuitBreaker(failure_threshold=3, timeout=0.1)

        # Cycle 1: Open circuit
        fail_call(breaker)
        fail_call(breaker)
        fail_call(breaker)
        assert breaker.state == CircuitState.OPEN

        # Recover
        time.sleep(0.15)
        admission_available(breaker)
        succeed_call(breaker)
        assert breaker.state == CircuitState.CLOSED

        # Cycle 2: Open circuit again
        fail_call(breaker)
        fail_call(breaker)
        fail_call(breaker)
        assert breaker.state == CircuitState.OPEN

        # Recover again
        time.sleep(0.15)
        admission_available(breaker)
        succeed_call(breaker)
        assert breaker.state == CircuitState.CLOSED

    def test_half_open_failure_recovery(self):
        """Test recovery when HALF_OPEN state fails again."""
        breaker = CircuitBreaker(failure_threshold=3, timeout=0.1)

        # Open circuit and transition to HALF_OPEN
        fail_call(breaker)
        fail_call(breaker)
        fail_call(breaker)
        time.sleep(0.15)
        admission_available(breaker)
        assert breaker.state == CircuitState.HALF_OPEN

        # Failure in HALF_OPEN should open circuit
        fail_call(breaker)
        assert breaker.state == CircuitState.OPEN

        # Recover
        time.sleep(0.15)
        admission_available(breaker)
        succeed_call(breaker)
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
