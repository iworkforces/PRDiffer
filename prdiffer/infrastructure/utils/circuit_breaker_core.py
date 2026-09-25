"""Circuit breaker core implementation."""

import time
import threading
import logging
from enum import StrEnum

import anyio

from prdiffer.infrastructure.logging.console_logger import get_logger, ConsoleLogger


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit open, fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """Thread-safe circuit breaker implementation for API resilience.

    Prevents cascading failures by temporarily stopping calls to a failing service
    and allowing it time to recover.

    Thread Safety:
    - Uses one threading lock for synchronous and asynchronous operations
    - Async operations acquire it in worker threads, never on the event loop
    """

    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0, logger: logging.Logger | ConsoleLogger | None = None) -> None:
        """Initialize the circuit breaker.

        Args:
            failure_threshold: Number of failures before opening the circuit
            timeout: Time in seconds to keep circuit open before trying again
            logger: Logger instance for circuit breaker events
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self._logger = logger or get_logger()

        self._sync_lock = threading.Lock()

        # Circuit state (protected by the shared lock)
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._successful_calls = 0

    @property
    def state(self) -> CircuitState:
        """Get current circuit state (thread-safe read)."""
        with self._sync_lock:
            return self._state

    @property
    def failure_count(self) -> int:
        """Get current failure count (thread-safe read)."""
        with self._sync_lock:
            return self._failure_count

    def can_execute(self) -> bool:
        """Check if execution is allowed based on circuit state (thread-safe).

        Returns:
            bool: True if execution is allowed, False otherwise
        """
        with self._sync_lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Check if timeout has elapsed
                if self._last_failure_time and time.time() - self._last_failure_time >= self.timeout:
                    self._transition_to_half_open_unlocked()
                else:
                    return False
            else:
                return True
        self._logger.info("Circuit breaker transitioned to HALF_OPEN: Testing service recovery")
        return True

    async def can_execute_async(self) -> bool:
        """Async version of can_execute (non-blocking).

        Returns:
            bool: True if execution is allowed, False otherwise
        """
        return await anyio.to_thread.run_sync(self.can_execute)

    def record_success(self) -> None:
        """Record a successful operation (thread-safe)."""
        with self._sync_lock:
            message = self._record_success_unlocked()
        if message:
            if message[0]:
                self._logger.info(message[1])
            else:
                self._logger.debug(message[1])

    async def record_success_async(self) -> None:
        """Async version of record_success (non-blocking)."""
        await anyio.to_thread.run_sync(self.record_success)

    def _record_success_unlocked(self) -> tuple[bool, str] | None:
        """Record a successful operation (must be called with lock held)."""
        if self._state == CircuitState.HALF_OPEN:
            self._successful_calls += 1
            # Reset circuit after successful call in half-open state
            self._transition_to_closed_unlocked()
            return True, "Circuit breaker CLOSED: Service recovered"
        elif self._state == CircuitState.CLOSED:
            # Reset failure count on success
            if self._failure_count > 0:
                self._failure_count = 0
                return False, "Circuit breaker: Reset failure count after success"
        return None

    def record_failure(self) -> None:
        """Record a failed operation (thread-safe)."""
        with self._sync_lock:
            opened = self._record_failure_unlocked()
            failure_count = self._failure_count
        if opened:
            self._logger.warning(f"Circuit breaker OPENED: {failure_count} failures reached threshold {self.failure_threshold}")

    async def record_failure_async(self) -> None:
        """Async version of record_failure (non-blocking)."""
        await anyio.to_thread.run_sync(self.record_failure)

    def _record_failure_unlocked(self) -> bool:
        """Record a failed operation (must be called with lock held)."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.CLOSED:
            if self._failure_count >= self.failure_threshold:
                self._transition_to_open_unlocked()
                return True
        elif self._state == CircuitState.HALF_OPEN:
            # Failure in half-open state, go back to open
            self._transition_to_open_unlocked()
            return True
        return False

    def _transition_to_open(self) -> None:
        """Transition circuit to OPEN state (thread-safe)."""
        with self._sync_lock:
            self._transition_to_open_unlocked()
            failure_count = self._failure_count
        self._logger.warning(f"Circuit breaker OPENED: {failure_count} failures reached threshold {self.failure_threshold}")

    def _transition_to_open_unlocked(self) -> None:
        """Transition circuit to OPEN state (must be called with lock held)."""
        self._state = CircuitState.OPEN

    def _transition_to_half_open(self) -> None:
        """Transition circuit to HALF_OPEN state (thread-safe)."""
        with self._sync_lock:
            self._transition_to_half_open_unlocked()
        self._logger.info("Circuit breaker transitioned to HALF_OPEN: Testing service recovery")

    def _transition_to_half_open_unlocked(self) -> None:
        """Transition circuit to HALF_OPEN state (must be called with lock held)."""
        self._state = CircuitState.HALF_OPEN
        self._successful_calls = 0

    def _transition_to_closed(self) -> None:
        """Transition circuit to CLOSED state (thread-safe)."""
        with self._sync_lock:
            self._transition_to_closed_unlocked()
        self._logger.info("Circuit breaker CLOSED: Service recovered")

    def _transition_to_closed_unlocked(self) -> None:
        """Transition circuit to CLOSED state (must be called with lock held)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._successful_calls = 0

    def get_stats(self) -> dict[str, object]:
        """Get circuit breaker statistics (thread-safe).

        Returns:
            dict: Circuit breaker statistics
        """
        with self._sync_lock:
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "timeout": self.timeout,
                "last_failure_time": self._last_failure_time,
                "successful_calls": self._successful_calls,
            }


class CircuitBreakerOpenException(Exception):
    """Exception raised when circuit breaker is open."""

    def __init__(self, message: str = "Circuit breaker is open") -> None:
        self.message = message
        super().__init__(self.message)
