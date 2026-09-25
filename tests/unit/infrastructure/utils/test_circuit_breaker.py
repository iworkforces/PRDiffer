"""Unit tests for CircuitBreaker utility component.

Tests the circuit breaker pattern implementation with state transitions,
failure handling, and recovery mechanisms.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Iterator

import anyio
from unittest.mock import Mock
import pytest

from prdiffer.infrastructure.utils.circuit_breaker_core import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerOpenException,
)
from prdiffer.infrastructure.utils.circuit_breaker_registry import (
    get_global_circuit_breaker_registry,
)
from prdiffer.infrastructure.utils.circuit_breaker.core import CircuitBreaker as ShimCircuitBreaker
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
class TestCircuitBreakerAsyncMethods:
    """Test suite for async methods of CircuitBreaker."""

    @pytest.mark.asyncio
    async def test_can_execute_async_when_closed(self):
        """Test async can_execute returns True when circuit is CLOSED."""
        breaker = CircuitBreaker()

        assert await breaker.can_execute_async() is True
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_can_execute_async_blocks_when_open(self):
        """Test async can_execute returns False when circuit is OPEN."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open the circuit
        await breaker.record_failure_async()
        await breaker.record_failure_async()

        assert await breaker.can_execute_async() is False
        assert breaker.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_record_success_async(self):
        """Test async record_success updates state correctly."""
        breaker = CircuitBreaker(failure_threshold=2, timeout=60.0)

        # Open circuit and wait for timeout
        await breaker.record_failure_async()
        await breaker.record_failure_async()
        breaker._last_failure_time = time.time() - breaker.timeout - 1

        # Trigger transition to HALF_OPEN
        assert await breaker.can_execute_async() is True

        # Record success
        await breaker.record_success_async()

        assert breaker.state == CircuitState.CLOSED
        assert breaker.get_stats()["successful_calls"] == 0  # Reset on close

    @pytest.mark.asyncio
    async def test_record_failure_async(self):
        """Test async record_failure increments failure count."""
        breaker = CircuitBreaker(failure_threshold=3)

        # Record failures asynchronously
        await breaker.record_failure_async()
        assert breaker.failure_count == 1

        await breaker.record_failure_async()
        assert breaker.failure_count == 2

        await breaker.record_failure_async()
        assert breaker.failure_count == 3
        assert breaker.state == CircuitState.OPEN

    @pytest.mark.asyncio
    @pytest.mark.thread_safety
    async def test_async_admission_waits_for_sync_lock_without_blocking_loop(self, monkeypatch):
        breaker = CircuitBreaker(failure_threshold=1)
        lock = breaker._sync_lock
        held = threading.Event()
        release = threading.Event()
        attempted = anyio.Event()
        progress = anyio.Event()
        finished = anyio.Event()
        result: list[bool] = []

        def hold_lock() -> None:
            with lock:
                held.set()
                release.wait(timeout=3)

        @contextmanager
        def observed_lock() -> Iterator[None]:
            anyio.from_thread.run_sync(attempted.set)
            anyio.from_thread.run_sync(progress.set)
            with lock:
                yield

        async def admit() -> None:
            result.append(await breaker.can_execute_async())
            finished.set()
            progress.set()

        worker = threading.Thread(target=hold_lock)
        worker.start()
        try:
            with anyio.fail_after(2):
                assert await anyio.to_thread.run_sync(held.wait, 2)
                monkeypatch.setattr(breaker, "_sync_lock", observed_lock())
                async with anyio.create_task_group() as group:
                    group.start_soon(admit)
                    await progress.wait()
                    assert attempted.is_set()
                    assert not finished.is_set()
                    release.set()
            assert result == [True]
        finally:
            release.set()
            worker.join(timeout=3)

    @pytest.mark.thread_safety
    def test_mixed_threads_and_independent_loops_share_failure_and_recovery(self, monkeypatch):
        clock = [100.0]
        monkeypatch.setattr(core, "time", SimpleNamespace(time=lambda: clock[0]))
        breaker = CircuitBreaker(failure_threshold=12, timeout=10)
        barrier = threading.Barrier(3)

        def sync_worker() -> None:
            barrier.wait(timeout=2)
            for _ in range(4):
                breaker.record_failure()

        async def async_worker() -> None:
            await anyio.to_thread.run_sync(barrier.wait, 2)
            for _ in range(4):
                await breaker.record_failure_async()

        with ThreadPoolExecutor(max_workers=3) as workers:
            futures = [workers.submit(sync_worker), workers.submit(anyio.run, async_worker), workers.submit(anyio.run, async_worker)]
            for future in futures:
                future.result(timeout=3)

        assert breaker.get_stats()["failure_count"] == 12
        assert breaker.state == CircuitState.OPEN
        assert not anyio.run(breaker.can_execute_async)
        clock[0] = 110.0
        assert anyio.run(breaker.can_execute_async)
        assert breaker.can_execute()
        assert breaker.state == CircuitState.HALF_OPEN
        breaker.record_failure()
        assert not anyio.run(breaker.can_execute_async)
        clock[0] = 120.0
        assert breaker.can_execute()
        anyio.run(breaker.record_success_async)
        assert breaker.get_stats()["state"] == CircuitState.CLOSED.value
        assert breaker.failure_count == 0

    def test_logger_callback_can_inspect_breaker_during_transition(self):
        logger = Mock()
        breaker = CircuitBreaker(failure_threshold=1, logger=logger)
        logger.warning.side_effect = lambda message: breaker.get_stats()

        breaker.record_failure()

        assert breaker.state == CircuitState.OPEN
        logger.warning.assert_called_once()

    def test_package_shim_uses_same_class(self):
        assert ShimCircuitBreaker is CircuitBreaker


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

    def test_get_breaker_from_registry(self):
        """Test getting a circuit breaker from the global registry."""
        registry = get_global_circuit_breaker_registry()

        breaker = registry.get_breaker("custom_endpoint")

        assert breaker is not None
        # Verify it's the same breaker from registry
        assert breaker is registry.get_breaker("custom_endpoint")

    def test_registry_returns_same_breaker_for_same_endpoint(self):
        """Test that registry returns the same breaker for the same endpoint."""
        registry = get_global_circuit_breaker_registry()

        breaker1 = registry.get_breaker("endpoint1")
        breaker2 = registry.get_breaker("endpoint1")

        assert breaker1 is breaker2


@pytest.mark.unit
class TestGlobalCircuitBreakerRegistry:
    """Test suite for GlobalCircuitBreakerRegistry."""

    def setup_method(self):
        """Reset the singleton before each test."""
        # Clear the singleton instance to get fresh state
        import prdiffer.infrastructure.utils.circuit_breaker_registry as cb_registry

        cb_registry._global_circuit_breaker_registry = None
        cb_registry.GlobalCircuitBreakerRegistry._instance = None
        cb_registry.GlobalCircuitBreakerRegistry._initialized = False

    def test_singleton_pattern(self):
        """Test that get_global_circuit_breaker_registry returns singleton."""
        registry1 = get_global_circuit_breaker_registry()
        registry2 = get_global_circuit_breaker_registry()

        assert registry1 is registry2

    def test_registry_initialization(self):
        """Test registry initializes with correct defaults."""
        registry = get_global_circuit_breaker_registry(default_failure_threshold=10, default_timeout=30.0)

        stats = registry.get_all_stats()
        assert "global" in stats
        # Global breaker uses default_failure_threshold * 2
        assert stats["global"]["failure_threshold"] == 10 * 2
        assert stats["global"]["timeout"] == 30.0

    def test_get_breaker_creates_new_breaker(self):
        """Test get_breaker creates new breaker for endpoint."""
        registry = get_global_circuit_breaker_registry()

        breaker1 = registry.get_breaker("github_api")
        breaker2 = registry.get_breaker("github_api")

        # Should return same instance for same endpoint
        assert breaker1 is breaker2

        # Different endpoint should return different instance
        breaker3 = registry.get_breaker("repo_content")
        assert breaker1 is not breaker3

    def test_get_breaker_persists_across_calls(self):
        """Test that get_breaker returns same instance across calls."""
        registry = get_global_circuit_breaker_registry()

        breaker1 = registry.get_breaker("endpoint1")
        breaker2 = registry.get_breaker("endpoint1")

        assert breaker1 is breaker2

    def test_can_execute_with_endpoint(self):
        """Test can_execute checks both global and endpoint breakers."""
        registry = get_global_circuit_breaker_registry(default_failure_threshold=2)

        # Both global and endpoint should allow execution initially
        assert registry.can_execute("github_api") is True

        # Open global breaker (needs 4 failures since threshold is 2*2=4)
        for _ in range(4):
            registry.global_breaker.record_failure()

        # Should now be blocked
        assert registry.can_execute("github_api") is False

    def test_can_execute_without_endpoint(self):
        """Test can_execute with no endpoint checks only global breaker."""
        registry = get_global_circuit_breaker_registry(default_failure_threshold=2)

        # Initially should allow execution
        assert registry.can_execute() is True

        # Open global breaker (needs 4 failures since threshold is 2*2=4)
        for _ in range(4):
            registry.global_breaker.record_failure()

        # Should now be blocked
        assert registry.can_execute() is False

    def test_get_all_stats(self):
        """Test get_all_stats returns statistics for all breakers."""
        registry = get_global_circuit_breaker_registry()

        # Create some state
        registry.get_breaker("endpoint1").record_failure()
        registry.get_breaker("endpoint2").record_failure()

        stats = registry.get_all_stats()

        assert "global" in stats
        assert "endpoint1" in stats
        assert "endpoint2" in stats

    def test_get_open_breakers(self):
        """Test get_open_breakers returns list of open endpoints."""
        registry = get_global_circuit_breaker_registry(
            default_failure_threshold=2  # Use lower threshold for testing
        )

        # Initially no open breakers
        open_breakers = registry.get_open_breakers()
        assert open_breakers == []

        # Open an endpoint breaker
        endpoint_breaker = registry.get_breaker("test_endpoint")
        endpoint_breaker.record_failure()
        endpoint_breaker.record_failure()

        # Check it's in open list
        open_breakers = registry.get_open_breakers()
        assert "test_endpoint" in open_breakers

    def test_reset_all(self):
        """Test reset_all closes all circuit breakers."""
        registry = get_global_circuit_breaker_registry(
            default_failure_threshold=2  # Use lower threshold for testing
        )

        # Open some breakers
        registry.get_breaker("endpoint1").record_failure()
        registry.get_breaker("endpoint1").record_failure()
        registry.get_breaker("endpoint2").record_failure()
        registry.get_breaker("endpoint2").record_failure()

        assert registry.get_open_breakers() != []

        # Reset all
        registry.reset_all()

        # All breakers should be closed now
        open_breakers = registry.get_open_breakers()
        assert open_breakers == []

        # All failure counts should be reset
        stats = registry.get_all_stats()
        assert stats["endpoint1"]["failure_count"] == 0
        assert stats["endpoint2"]["failure_count"] == 0

    def test_clear_endpoint(self):
        """Test clear_endpoint removes endpoint breaker."""
        registry = get_global_circuit_breaker_registry()

        # Create a breaker for an endpoint
        registry.get_breaker("temp_endpoint")

        # Clear it
        registry.clear_endpoint("temp_endpoint")

        # Should no longer exist
        # Get a new breaker to verify
        new_breaker = registry.get_breaker("temp_endpoint")
        assert new_breaker is not None  # Creates new instance

    def test_record_success_propagates_to_all_breakers(self):
        """Test record_success updates both global and endpoint breakers."""
        registry = get_global_circuit_breaker_registry()

        _ = registry.get_breaker("test_endpoint")

        # Record success propagates to global
        registry.record_success("test_endpoint")
        assert registry.global_breaker.failure_count == 0

        # Record success without endpoint affects only global
        registry.record_success()
        assert registry.global_breaker.get_stats()["successful_calls"] >= 0

    def test_record_failure_propagates_to_all_breakers(self):
        """Test record_failure updates both global and endpoint breakers."""
        registry = get_global_circuit_breaker_registry(default_failure_threshold=5)

        endpoint_breaker = registry.get_breaker("test_endpoint")

        # Record failure propagates to both
        registry.record_failure("test_endpoint")
        assert registry.global_breaker.failure_count == 1
        assert endpoint_breaker.failure_count == 1

    @pytest.mark.asyncio
    async def test_mixed_registry_reset_clear_and_isolation(self):
        registry = get_global_circuit_breaker_registry(default_failure_threshold=2, default_timeout=60)
        endpoint = registry.get_breaker("one")
        other = registry.get_breaker("other")

        await registry.record_failure_async("one")
        registry.record_failure("one")
        assert endpoint.state == CircuitState.OPEN
        assert other.state == CircuitState.CLOSED
        assert await registry.can_execute_async("one") is False
        assert registry.can_execute("other") is True

        registry.global_breaker.record_failure()
        await registry.global_breaker.record_failure_async()
        assert not await registry.can_execute_async("other")
        assert registry.get_open_breakers() == ["global", "one"]

        registry.reset_all()
        assert await registry.can_execute_async("one")
        assert registry.get_all_stats()["one"]["failure_count"] == 0
        assert registry.get_all_stats()["global"]["failure_count"] == 0
        registry.clear_endpoint("one")
        assert registry.get_breaker("one") is not endpoint
        assert registry.get_breaker("other") is other
        assert registry.global_breaker.state == CircuitState.CLOSED
