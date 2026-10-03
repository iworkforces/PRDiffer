"""Health monitoring component for server status and metrics."""

import logging
from typing import Any
from prdiffer.domain.interfaces.protocols import (
    HealthMonitorProtocol,
    MetricsTrackerProtocol,
    RateLimiterProtocol,
)
from prdiffer.domain.services.logger import LoggerServiceInterface


class HealthMonitor(HealthMonitorProtocol):
    """Component responsible for health monitoring and status checks."""

    def __init__(
        self,
        metrics_tracker: MetricsTrackerProtocol,
        rate_limiter: RateLimiterProtocol,
        logger: logging.Logger | LoggerServiceInterface | None = None,
    ):
        self._metrics_tracker = metrics_tracker
        self._rate_limiter = rate_limiter
        self._logger = logger or logging.getLogger(__name__)

    def check_health(self) -> dict[str, Any]:
        """Perform health check and return status."""
        try:
            metrics = self._metrics_tracker.get_metrics_summary()

            rate_limit_info = self._rate_limiter.get_rate_limit_info()

            status = "healthy"

            # Degraded if success rate below 80%
            if metrics.get("total_requests", 0) > 0 and metrics.get("success_rate", 100) < 80:
                status = "degraded"

            # Degraded if rate limit nearly exhausted (<10% remaining)
            remaining_requests = rate_limit_info.get("remaining_requests", 0)
            max_requests = rate_limit_info.get("max_requests", 100)
            if remaining_requests / max_requests < 0.1:
                status = "degraded"

            health_data: dict[str, Any] = {
                "status": status,
                "uptime_seconds": metrics.get("uptime_seconds", 0),
                "uptime_human": metrics.get("uptime_human", "0s"),
                "total_requests": metrics.get("total_requests", 0),
                "successful_requests": metrics.get("successful_requests", 0),
                "failed_requests": metrics.get("failed_requests", 0),
                "success_rate": metrics.get("success_rate", 0.0),
                "current_rate": rate_limit_info.get("current_requests", 0),
                "rate_limit": rate_limit_info.get("max_requests", 100),
                "rate_limit_window": rate_limit_info.get("window_seconds", 60),
                "remaining_requests": rate_limit_info.get("remaining_requests", 0),
                "operations": metrics.get("operations", {}),
            }

            self._logger.debug(
                "Health check completed",
                extra={"status": status, "metrics": health_data},
            )
            return health_data

        except Exception as e:
            self._logger.error("Health check failed", extra={"error": str(e)})
            return {
                "status": "unhealthy",
                "error": str(e),
                "uptime_seconds": 0,
                "uptime_human": "unknown",
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "success_rate": 0.0,
                "current_rate": 0,
                "rate_limit": 100,
                "rate_limit_window": 60,
                "remaining_requests": 0,
                "operations": {},
            }

    def get_detailed_status(self) -> dict[str, Any]:
        """Get detailed health status including component-specific information."""
        health_status = self.check_health()

        health_status["components"] = {
            "metrics_tracker": {
                "status": "healthy",
                "description": "Tracking request metrics and performance",
            },
            "rate_limiter": {
                "status": "healthy",
                "description": "Managing request rate limiting",
            },
        }

        return health_status
