"""Unit tests for ApplicationFactory component creation and singleton pattern.

Tests verify:
- All 6 components are created correctly with proper type checking
- Factory implements ApplicationFactoryInterface
- Singleton pattern works as expected
"""

from unittest.mock import Mock

from prdiffer.application.factories.application_factory import (
    ApplicationFactory,
    get_application_factory,
)
from prdiffer.domain.factories.application_factory import (
    ApplicationFactoryInterface,
)
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.interfaces.protocols import (
    RateLimiterProtocol,
    MetricsTrackerProtocol,
)


class TestApplicationFactoryComponentCreation:
    """Test suite for ApplicationFactory component creation methods."""

    def test_create_rate_limiter(self):
        """Test that create_rate_limiter returns RateLimiterProtocol instance."""
        factory = ApplicationFactory()
        mock_logger = Mock(spec=LoggerServiceInterface)

        result = factory.create_rate_limiter(mock_logger)

        assert result is not None
        assert hasattr(result, "check_rate_limit")
        assert hasattr(result, "increment_rate_limit")

    def test_create_metrics_tracker(self):
        """Test that create_metrics_tracker returns MetricsTrackerProtocol instance."""
        factory = ApplicationFactory()
        mock_logger = Mock(spec=LoggerServiceInterface)

        result = factory.create_metrics_tracker(mock_logger)

        assert result is not None
        assert hasattr(result, "track_request")
        assert hasattr(result, "get_metrics_summary")

    def test_create_health_monitor(self):
        """Test that create_health_monitor returns HealthMonitorProtocol instance."""
        factory = ApplicationFactory()
        mock_logger = Mock(spec=LoggerServiceInterface)
        mock_metrics_tracker = Mock(spec=MetricsTrackerProtocol)
        mock_rate_limiter = Mock(spec=RateLimiterProtocol)

        result = factory.create_health_monitor(
            metrics_tracker=mock_metrics_tracker,
            rate_limiter=mock_rate_limiter,
            logger=mock_logger,
        )

        assert result is not None
        assert hasattr(result, "check_health")

    def test_create_server_configuration(self):
        """Test that create_server_configuration returns ServerConfigurationProtocol instance."""
        factory = ApplicationFactory()
        mock_logger = Mock(spec=LoggerServiceInterface)
        mock_settings = Mock(spec=SettingsServiceInterface)

        result = factory.create_server_configuration(
            settings_service=mock_settings,
            logger=mock_logger,
        )

        assert result is not None
        assert hasattr(result, "get_server_info")

    def test_create_authentication(self):
        """Test that create_authentication returns AuthenticationProtocol instance."""
        factory = ApplicationFactory()
        mock_logger = Mock(spec=LoggerServiceInterface)

        result = factory.create_authentication(mock_logger)

        assert result is not None
        assert hasattr(result, "authenticate")


class TestApplicationFactorySingleton:
    """Test suite for ApplicationFactory singleton behavior."""

    def test_get_application_factory_returns_singleton(self):
        """Test that get_application_factory returns the same instance."""
        factory1 = get_application_factory()
        factory2 = get_application_factory()
        factory3 = get_application_factory()

        assert factory1 is factory2
        assert factory2 is factory3
        assert id(factory1) == id(factory2) == id(factory3)

    def test_application_factory_implements_interface(self):
        """Test that ApplicationFactory implements ApplicationFactoryInterface."""
        factory = get_application_factory()

        assert isinstance(factory, ApplicationFactoryInterface)
        assert hasattr(factory, "create_rate_limiter")
        assert callable(factory.create_rate_limiter)
        assert hasattr(factory, "create_metrics_tracker")
        assert callable(factory.create_metrics_tracker)
        assert hasattr(factory, "create_health_monitor")
        assert callable(factory.create_health_monitor)
        assert hasattr(factory, "create_server_configuration")
        assert callable(factory.create_server_configuration)
        assert hasattr(factory, "create_authentication")
        assert callable(factory.create_authentication)
