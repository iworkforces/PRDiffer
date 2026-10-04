"""Health and metrics endpoints for FastMCP server monitoring."""

from collections.abc import Callable, Awaitable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.interfaces.protocols import (
    MetricsTrackerProtocol,
    HealthMonitorProtocol,
    AuthenticationProtocol,
)
from prdiffer.domain.interfaces.request_coalescing import RequestCoalescingProtocol


class HealthEndpoints:
    """Handler for health and metrics endpoints."""

    def __init__(
        self,
        health_monitor: HealthMonitorProtocol,
        metrics_tracker: MetricsTrackerProtocol,
        cache_service: CacheServiceInterface,
        authentication: AuthenticationProtocol,
        request_coalescing: RequestCoalescingProtocol,
        logger: LoggerServiceInterface,
    ):
        self._health_monitor = health_monitor
        self._metrics_tracker = metrics_tracker
        self._cache_service = cache_service
        self._authentication = authentication
        self._request_coalescing = request_coalescing
        self._logger = logger

    async def _get_health_status(self) -> dict[str, Any]:
        """Get health status and metrics for the MCP server."""
        # Project each component independently; diagnostic identifiers and errors
        # remain available internally but never cross this unauthenticated boundary.
        monitor = self._health_monitor.check_health()
        health_status = {key: monitor[key] for key in (
            "status", "uptime_seconds", "uptime_human", "total_requests", "successful_requests", "failed_requests",
            "success_rate", "current_rate", "rate_limit", "rate_limit_window", "remaining_requests",
        ) if key in monitor}
        authentication = self._authentication.get_status()
        health_status["authentication"] = {key: authentication[key] for key in (
            "authentication_enabled", "api_keys_configured", "admin_api_key_configured",
        ) if key in authentication}
        cache = self._cache_service.get_stats()
        health_status["cache"] = {key: cache[key] for key in (
            "cache_size", "cache_bytes", "cache_max_bytes", "cache_hits", "cache_misses", "cache_expirations",
            "cache_evictions_ttl", "cache_evictions_size",
        ) if key in cache}
        coalescing = await self._request_coalescing.get_stats()
        health_status["request_coalescing"] = {key: coalescing[key] for key in (
            "pending_count", "total_waiters",
        ) if key in coalescing}
        return health_status

    def get_health_handler(self) -> Callable[[], Awaitable[dict[str, Any]]]:
        """Return health handler function for FastMCP registration."""

        async def health() -> dict[str, Any]:
            """Get server health status and metrics."""
            try:
                return await self._get_health_status()
            except (RuntimeError, KeyError, AttributeError) as e:
                self._logger.error(
                    "Failed to get health status",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return {"status": "unhealthy"}

        return health

    def get_metrics_handler(self) -> Callable[[Request], Awaitable[JSONResponse]]:
        """Return metrics handler function for FastMCP registration."""

        async def metrics_handler(request: Request) -> JSONResponse:
            """Handle metrics endpoint requests."""
            try:
                metrics = self._metrics_tracker.get_metrics_summary()
                return JSONResponse(metrics)
            except (RuntimeError, KeyError, AttributeError) as e:
                self._logger.error(
                    "Failed to get metrics",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return JSONResponse(
                    {
                        "server": "prdiffer",
                        "error": "Failed to get metrics",
                        "message": "Internal server error",
                    },
                    status_code=500,
                )

        return metrics_handler

    def _create_safe_error_message(self, exception: Exception) -> str:
        """Create a safe error message that doesn't expose internal details."""
        safe_messages = {
            "GithubException": "GitHub API error occurred",
            "RateLimitExceededException": "API rate limit exceeded. Please try again later",
            "UnknownObjectException": "Repository or PR not found",
            "BadCredentialsException": "GitHub authentication failed",
            "TwoFactorException": "Two-factor authentication required",
            "InvalidURLError": "Invalid GitHub PR URL format",
            "InvalidRepositoryError": "Invalid repository identifier",
            "InvalidPRNumberError": "Invalid pull request number",
            "InputSanitizationError": "Invalid input parameters",
            "SuspiciousOperationError": "Request contains suspicious patterns",
            "ConnectionError": "Connection to GitHub failed",
            "TimeoutError": "Request timed out",
            "SSLError": "Secure connection failed",
            "ValueError": "Invalid input value",
            "TypeError": "Invalid input type",
            "KeyError": "Missing required field",
            "AttributeError": "Configuration error",
        }

        exception_type = type(exception).__name__

        if exception_type in safe_messages:
            return safe_messages[exception_type]

        return "Request processing failed"
