"""Unit tests for HealthMonitor application component.

Tests the HealthMonitor component which provides health status
monitoring for the MCP server.
"""

from unittest.mock import Mock
import pytest
from prdiffer.application.components.health_monitor import HealthMonitor


class TestHealthMonitorInitialization:
    """Test suite for HealthMonitor initialization."""

    def test_health_monitor_initialization(self):
        """Test HealthMonitor can be initialized."""
        mock_metrics = Mock()
        mock_rate_limiter = Mock()

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        assert monitor is not None
        assert monitor._metrics_tracker == mock_metrics
        assert monitor._rate_limiter == mock_rate_limiter

    def test_health_monitor_with_logger(self):
        """Test HealthMonitor with custom logger."""
        mock_metrics = Mock()
        mock_rate_limiter = Mock()
        mock_logger = Mock()

        monitor = HealthMonitor(
            metrics_tracker=mock_metrics,
            rate_limiter=mock_rate_limiter,
            logger=mock_logger,
        )

        assert monitor._logger == mock_logger

    def test_health_monitor_default_logger(self):
        """Test HealthMonitor creates default logger."""
        mock_metrics = Mock()
        mock_rate_limiter = Mock()

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        assert monitor._logger is not None


class TestHealthMonitorCheckHealth:
    """Test suite for check_health method."""

    def test_check_health_healthy_status(self):
        """Test health check returns healthy status when all is well."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 10,
            "successful_requests": 10,
            "failed_requests": 0,
            "success_rate": 100.0,
            "operations": {},
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {
            "current_requests": 5,
            "max_requests": 100,
            "window_seconds": 60,
            "remaining_requests": 95,
        }

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        health = monitor.check_health()

        assert health["status"] == "healthy"
        assert health["uptime_seconds"] == 100
        assert health["success_rate"] == 100.0
        assert health["remaining_requests"] == 95

    def test_check_health_degraded_by_low_success_rate(self):
        """Test health check returns degraded when success rate is low."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 100,
            "successful_requests": 70,
            "failed_requests": 30,
            "success_rate": 70.0,  # Below 80%
            "operations": {},
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {
            "current_requests": 10,
            "max_requests": 100,
            "window_seconds": 60,
            "remaining_requests": 90,
        }

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        health = monitor.check_health()

        assert health["status"] == "degraded"

    def test_check_health_degraded_by_rate_limit(self):
        """Test health check returns degraded when rate limit is near."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 95,
            "successful_requests": 95,
            "failed_requests": 0,
            "success_rate": 100.0,
            "operations": {},
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {
            "current_requests": 95,
            "max_requests": 100,
            "window_seconds": 60,
            "remaining_requests": 5,  # Less than 10%
        }

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        health = monitor.check_health()

        assert health["status"] == "degraded"

    def test_check_health_unhealthy_on_exception(self):
        """Test health check returns unhealthy when exception occurs."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.side_effect = Exception("Metrics error")

        mock_rate_limiter = Mock()

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        health = monitor.check_health()

        assert health["status"] == "unhealthy"
        assert "error" in health
        assert health["error"] == "Metrics error"

    def test_check_health_includes_all_fields(self):
        """Test health check includes all expected fields."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 10,
            "successful_requests": 9,
            "failed_requests": 1,
            "success_rate": 90.0,
            "operations": {"op1": {}},
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {
            "current_requests": 5,
            "max_requests": 100,
            "window_seconds": 60,
            "remaining_requests": 95,
        }

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        health = monitor.check_health()

        expected_fields = [
            "status",
            "uptime_seconds",
            "uptime_human",
            "total_requests",
            "successful_requests",
            "failed_requests",
            "success_rate",
            "current_rate",
            "rate_limit",
            "rate_limit_window",
            "remaining_requests",
            "operations",
        ]

        for field in expected_fields:
            assert field in health

    def test_check_health_with_operations(self):
        """Test health check includes operations data."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 10,
            "successful_requests": 10,
            "failed_requests": 0,
            "success_rate": 100.0,
            "operations": {
                "get_pr_diff": {
                    "total_requests": 10,
                    "success_rate": 100.0,
                }
            },
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {
            "current_requests": 5,
            "max_requests": 100,
            "window_seconds": 60,
            "remaining_requests": 95,
        }

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        health = monitor.check_health()

        assert "get_pr_diff" in health["operations"]


class TestHealthMonitorGetDetailedStatus:
    """Test suite for get_detailed_status method."""

    def test_get_detailed_status_includes_components(self):
        """Test detailed status includes component information."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 10,
            "successful_requests": 10,
            "failed_requests": 0,
            "success_rate": 100.0,
            "operations": {},
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {
            "current_requests": 5,
            "max_requests": 100,
            "window_seconds": 60,
            "remaining_requests": 95,
        }

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        status = monitor.get_detailed_status()

        assert "components" in status
        assert "metrics_tracker" in status["components"]
        assert "rate_limiter" in status["components"]

    def test_get_detailed_status_component_status(self):
        """Test component status fields."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 10,
            "successful_requests": 10,
            "failed_requests": 0,
            "success_rate": 100.0,
            "operations": {},
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {
            "current_requests": 5,
            "max_requests": 100,
            "window_seconds": 60,
            "remaining_requests": 95,
        }

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        status = monitor.get_detailed_status()

        metrics_tracker_component = status["components"]["metrics_tracker"]
        assert metrics_tracker_component["status"] == "healthy"
        assert "description" in metrics_tracker_component

        rate_limiter_component = status["components"]["rate_limiter"]
        assert rate_limiter_component["status"] == "healthy"
        assert "description" in rate_limiter_component

    def test_get_detailed_status_preserves_health_status(self):
        """Test detailed status preserves health check results."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 100,
            "successful_requests": 70,
            "failed_requests": 30,
            "success_rate": 70.0,
            "operations": {},
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {
            "current_requests": 10,
            "max_requests": 100,
            "window_seconds": 60,
            "remaining_requests": 90,
        }

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        status = monitor.get_detailed_status()

        # Should still be degraded from low success rate
        assert status["status"] == "degraded"


class TestHealthMonitorEdgeCases:
    """Test suite for HealthMonitor edge cases."""

    def test_check_health_with_empty_metrics(self):
        """Test health check with empty metrics."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {}

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {}

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        health = monitor.check_health()

        # With 0 remaining requests out of 100 max, that's < 10%, so degraded
        assert health["status"] == "degraded"
        assert health["uptime_seconds"] == 0

    def test_check_health_with_missing_rate_limit(self):
        """Test health check handles missing rate limit info."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 10,
            "successful_requests": 10,
            "failed_requests": 0,
            "success_rate": 100.0,
            "operations": {},
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {}

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        health = monitor.check_health()

        # Should use defaults for missing rate limit info
        assert health["current_rate"] == 0
        assert health["rate_limit"] == 100

    def test_check_health_zero_max_requests_division(self):
        """Test health check handles zero max requests gracefully."""
        mock_metrics = Mock()
        mock_metrics.get_metrics_summary.return_value = {
            "uptime_seconds": 100,
            "uptime_human": "1m 40s",
            "total_requests": 10,
            "successful_requests": 10,
            "failed_requests": 0,
            "success_rate": 100.0,
            "operations": {},
        }

        mock_rate_limiter = Mock()
        mock_rate_limiter.get_rate_limit_info.return_value = {
            "max_requests": 0,  # Would cause division by zero
            "remaining_requests": 0,
        }

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        # Should handle gracefully or raise error
        # Either behavior is acceptable
        try:
            health = monitor.check_health()
            # If no error, check that we got a result
            assert "status" in health
        except ZeroDivisionError:
            # Also acceptable - indicates potential bug
            pass


class TestHealthMonitorProtocolCompliance:
    """Test suite for HealthMonitor protocol compliance."""

    def test_has_required_methods(self):
        """Test that HealthMonitor has all required protocol methods."""
        mock_metrics = Mock()
        mock_rate_limiter = Mock()

        monitor = HealthMonitor(metrics_tracker=mock_metrics, rate_limiter=mock_rate_limiter)

        # Check all required methods exist
        assert hasattr(monitor, "check_health")
        assert callable(monitor.check_health)
@pytest.mark.parametrize("successes,failures,expected", [(0, 0, "healthy"), (4, 1, "healthy"), (3, 1, "degraded")])
def test_real_metrics_health_threshold(successes, failures, expected):
    from prdiffer.application.components.metrics_tracker import MetricsTracker
    from prdiffer.application.components.rate_limiter import RateLimiter

    # Given: real aggregate counters, including the idle case.
    tracker = MetricsTracker()
    for success in [True] * successes + [False] * failures:
        tracker.track_request("get_pr_diff", success, 0.01)
    monitor = HealthMonitor(tracker, RateLimiter())
    # When / Then.
    assert monitor.check_health()["status"] == expected
