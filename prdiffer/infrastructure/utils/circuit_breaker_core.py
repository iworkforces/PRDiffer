"""Circuit breaker core implementation."""

import time
import threading
import logging
from enum import StrEnum
from dataclasses import dataclass

from prdiffer.infrastructure.logging.console_logger import get_logger, ConsoleLogger


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit open, fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass(frozen=True, slots=True, eq=False)
class CircuitBreakerPermit:
    """Identity-bearing admission, valid only in its breaker's generation."""

    generation: int


class CircuitBreaker:
    """Thread-safe circuit breaker implementation for API resilience.

    Prevents cascading failures by temporarily stopping calls to a failing service
    and allowing it time to recover.

    Thread Safety:
    - One threading lock guards all state; callers run on worker threads.
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
        self._generation = 0
        self._permits: set[CircuitBreakerPermit] = set()

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

    def acquire(self) -> CircuitBreakerPermit | None:
        """Reserve one logical call; only one recovery trial may be in flight."""
        transitioned = False
        with self._sync_lock:
            if self._state == CircuitState.OPEN:
                if self._last_failure_time is not None and time.time() - self._last_failure_time >= self.timeout:
                    self._transition_to_half_open_unlocked()
                    transitioned = True
                else:
                    return None
            if self._state == CircuitState.HALF_OPEN and self._permits:
                return None
            permit = CircuitBreakerPermit(self._generation)
            self._permits.add(permit)
        if transitioned:
            self._logger.info("Circuit breaker transitioned to HALF_OPEN: Testing service recovery")
        return permit

    def _settle_unlocked(self, permit: CircuitBreakerPermit) -> bool:
        if permit.generation != self._generation or permit not in self._permits:
            return False
        self._permits.remove(permit)
        return True

    def release(self, permit: CircuitBreakerPermit) -> None:
        """Neutral settlement leaves recovery undecided and admits a replacement."""
        with self._sync_lock:
            if self._settle_unlocked(permit) and self._state == CircuitState.HALF_OPEN:
                self._fence_unlocked()

    def record_success(self, permit: CircuitBreakerPermit) -> None:
        """Record a successful operation (thread-safe)."""
        with self._sync_lock:
            if not self._settle_unlocked(permit):
                return
            message = self._record_success_unlocked()
        if message:
            if message[0]:
                self._logger.info(message[1])
            else:
                self._logger.debug(message[1])

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

    def record_failure(self, permit: CircuitBreakerPermit) -> None:
        """Record a failed operation (thread-safe)."""
        with self._sync_lock:
            if not self._settle_unlocked(permit):
                return
            opened = self._record_failure_unlocked()
            failure_count = self._failure_count
        if opened:
            self._logger.warning(f"Circuit breaker OPENED: {failure_count} failures reached threshold {self.failure_threshold}")

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

    def _transition_to_open_unlocked(self) -> None:
        """Transition circuit to OPEN state (must be called with lock held)."""
        self._state = CircuitState.OPEN
        self._fence_unlocked()

    def _transition_to_half_open_unlocked(self) -> None:
        """Transition circuit to HALF_OPEN state (must be called with lock held)."""
        self._state = CircuitState.HALF_OPEN
        self._fence_unlocked()
        self._successful_calls = 0

    def _transition_to_closed_unlocked(self) -> None:
        """Transition circuit to CLOSED state (must be called with lock held)."""
        self._state = CircuitState.CLOSED
        self._fence_unlocked()
        self._failure_count = 0
        self._successful_calls = 0

    def _fence_unlocked(self) -> None:
        self._generation += 1
        self._permits.clear()

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
