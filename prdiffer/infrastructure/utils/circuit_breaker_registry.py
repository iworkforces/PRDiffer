"""Circuit breaker registry for managing multiple circuit breakers."""

import threading


from prdiffer.infrastructure.logging.console_logger import get_logger

from prdiffer.infrastructure.utils.circuit_breaker_core import (
    CircuitBreaker,
    CircuitState,
)


class GlobalCircuitBreakerRegistry:
    """Global registry for circuit breakers shared across all API clients.

    This singleton class manages circuit breakers for different endpoints,
    allowing shared state across the application for better failure coordination.

    Features:
    - Per-endpoint circuit breakers for targeted failure handling
    - Global circuit breaker for system-wide protection
    - Configurable default thresholds
    - Statistics aggregation across all breakers
    """

    _instance: "GlobalCircuitBreakerRegistry | None" = None
    _lock = threading.Lock()
    _initialized: bool = False

    def __new__(
        cls,
        default_failure_threshold: int = 5,
        default_timeout: float = 60.0,
    ) -> "GlobalCircuitBreakerRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance

        return cls._instance

    def __init__(
        self,
        default_failure_threshold: int = 5,
        default_timeout: float = 60.0,
    ):
        """Initialize the global circuit breaker registry.

        Args:
            default_failure_threshold: Default failures before opening circuit
            default_timeout: Default timeout for open circuits
        """
        if self._initialized:
            return

        self._default_failure_threshold = default_failure_threshold
        self._default_timeout = default_timeout
        self._breakers: dict[str, CircuitBreaker] = {}
        self._registry_lock = threading.Lock()
        self._logger = get_logger()
        self._max_breakers = 100  # DoS prevention: limit number of circuit breakers

        self._global_breaker = CircuitBreaker(
            failure_threshold=default_failure_threshold * 2,  # Higher threshold for global
            timeout=default_timeout,
            logger=self._logger,
        )

        self._initialized = True

    def get_breaker(self, endpoint: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a specific endpoint.

        Args:
            endpoint: Identifier for the endpoint (e.g., "github_api", "repo_content")

        Returns:
            CircuitBreaker: Circuit breaker for the endpoint
        """
        with self._registry_lock:
            if endpoint not in self._breakers:
                if len(self._breakers) >= self._max_breakers:
                    self._evict_oldest_breaker()

                self._breakers[endpoint] = CircuitBreaker(
                    failure_threshold=self._default_failure_threshold,
                    timeout=self._default_timeout,
                    logger=self._logger,
                )
                self._logger.debug(f"Created circuit breaker for endpoint: {endpoint} (total: {len(self._breakers)})")
            return self._breakers[endpoint]

    def _evict_oldest_breaker(self) -> None:
        """Evict the oldest CLOSED circuit breaker to make room for a new one."""
        for endpoint, breaker in self._breakers.items():
            if breaker.state == CircuitState.CLOSED:
                del self._breakers[endpoint]
                self._logger.info(f"Evicted CLOSED circuit breaker for endpoint '{endpoint}' to make room (max: {self._max_breakers})")
                return

        if self._breakers:
            oldest_endpoint = next(iter(self._breakers))
            oldest_breaker = self._breakers[oldest_endpoint]
            del self._breakers[oldest_endpoint]
            self._logger.warning(
                f"Evicted {oldest_breaker.state.value} circuit breaker for endpoint '{oldest_endpoint}' "
                f"to make room (no CLOSED breakers available, max: {self._max_breakers})"
            )

    @property
    def global_breaker(self) -> CircuitBreaker:
        return self._global_breaker

    def can_execute(self, endpoint: str | None = None) -> bool:
        """Check if execution is allowed for an endpoint.

        Checks both the endpoint-specific breaker and the global breaker.

        Args:
            endpoint: Specific endpoint to check (optional)

        Returns:
            bool: True if execution is allowed
        """
        if not self._global_breaker.can_execute():
            return False

        if endpoint:
            breaker = self.get_breaker(endpoint)
            if not breaker.can_execute():
                return False

        return True

    async def can_execute_async(self, endpoint: str | None = None) -> bool:
        """Async version of can_execute."""
        if not await self._global_breaker.can_execute_async():
            return False

        if endpoint:
            breaker = self.get_breaker(endpoint)
            if not await breaker.can_execute_async():
                return False

        return True

    def record_success(self, endpoint: str | None = None) -> None:
        """Record a successful operation.

        Args:
            endpoint: Specific endpoint that succeeded (optional)
        """
        self._global_breaker.record_success()
        if endpoint:
            self.get_breaker(endpoint).record_success()

    async def record_success_async(self, endpoint: str | None = None) -> None:
        """Async version of record_success."""
        await self._global_breaker.record_success_async()
        if endpoint:
            await self.get_breaker(endpoint).record_success_async()

    def record_failure(self, endpoint: str | None = None) -> None:
        """Record a failed operation.

        Args:
            endpoint: Specific endpoint that failed (optional)
        """
        self._global_breaker.record_failure()
        if endpoint:
            self.get_breaker(endpoint).record_failure()

    async def record_failure_async(self, endpoint: str | None = None) -> None:
        """Async version of record_failure."""
        await self._global_breaker.record_failure_async()
        if endpoint:
            await self.get_breaker(endpoint).record_failure_async()

    def get_all_stats(self) -> dict[str, dict[str, object]]:
        """Get statistics for all circuit breakers.

        Returns:
            Dict mapping endpoint names to their statistics
        """
        with self._registry_lock:
            stats = {"global": self._global_breaker.get_stats()}
            for endpoint, breaker in self._breakers.items():
                stats[endpoint] = breaker.get_stats()
            return stats

    def get_open_breakers(self) -> list[str]:
        """Get list of endpoints with open circuit breakers.

        Returns:
            List of endpoint names with open circuits
        """
        with self._registry_lock:
            open_breakers: list[str] = []
            if self._global_breaker.state == CircuitState.OPEN:
                open_breakers.append("global")
            for endpoint, breaker in self._breakers.items():
                if breaker.state == CircuitState.OPEN:
                    open_breakers.append(endpoint)
            return open_breakers

    def reset_all(self) -> None:
        """Reset all circuit breakers to closed state."""
        with self._registry_lock:
            self._global_breaker._transition_to_closed()
            for breaker in self._breakers.values():
                breaker._transition_to_closed()
            self._logger.info("All circuit breakers reset to CLOSED")

    def clear_endpoint(self, endpoint: str) -> None:
        """Remove a specific endpoint's circuit breaker.

        Args:
            endpoint: Endpoint to remove
        """
        with self._registry_lock:
            if endpoint in self._breakers:
                del self._breakers[endpoint]
                self._logger.debug(f"Removed circuit breaker for endpoint: {endpoint}")


_global_circuit_breaker_registry: GlobalCircuitBreakerRegistry | None = None


def get_global_circuit_breaker_registry(
    default_failure_threshold: int = 5,
    default_timeout: float = 60.0,
) -> GlobalCircuitBreakerRegistry:
    """Get the global circuit breaker registry singleton.

    Args:
        default_failure_threshold: Default failures before opening circuit
        default_timeout: Default timeout for open circuits

    Returns:
        GlobalCircuitBreakerRegistry: The global registry instance
    """
    global _global_circuit_breaker_registry

    if _global_circuit_breaker_registry is None:
        _global_circuit_breaker_registry = GlobalCircuitBreakerRegistry(
            default_failure_threshold=default_failure_threshold,
            default_timeout=default_timeout,
        )
    return _global_circuit_breaker_registry
