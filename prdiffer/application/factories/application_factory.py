"""Concrete application factory implementation for creating application-layer components."""

from prdiffer.domain.factories.application_factory import ApplicationFactoryInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.services.settings import SettingsServiceInterface

from prdiffer.domain.interfaces.protocols import (
    RateLimiterProtocol,
    MetricsTrackerProtocol,
    HealthMonitorProtocol,
    ServerConfigurationProtocol,
    AuthenticationProtocol,
)

from prdiffer.application.components.rate_limiter import RateLimiter
from prdiffer.application.components.metrics_tracker import MetricsTracker
from prdiffer.application.components.health_monitor import HealthMonitor
from prdiffer.application.components.server_configuration import ServerConfiguration
from prdiffer.application.components.authentication import AuthenticationMiddleware


class ApplicationFactory(ApplicationFactoryInterface):
    """Concrete implementation of application factory for creating application-layer components."""

    def create_rate_limiter(self, logger: LoggerServiceInterface) -> RateLimiterProtocol:
        return RateLimiter(logger=logger)

    def create_metrics_tracker(self, logger: LoggerServiceInterface) -> MetricsTrackerProtocol:
        return MetricsTracker(logger=logger)

    def create_health_monitor(
        self,
        metrics_tracker: MetricsTrackerProtocol,
        rate_limiter: RateLimiterProtocol,
        logger: LoggerServiceInterface,
    ) -> HealthMonitorProtocol:
        return HealthMonitor(
            metrics_tracker=metrics_tracker,
            rate_limiter=rate_limiter,
            logger=logger,
        )

    def create_server_configuration(
        self,
        settings_service: SettingsServiceInterface,
        logger: LoggerServiceInterface,
    ) -> ServerConfigurationProtocol:
        return ServerConfiguration(
            settings_service=settings_service,
            logger=logger,
        )

    def create_authentication(self, logger: LoggerServiceInterface) -> AuthenticationProtocol:
        return AuthenticationMiddleware(logger=logger)


_application_factory: ApplicationFactory | None = None


def get_application_factory() -> ApplicationFactoryInterface:
    """Get singleton instance of the application factory."""
    global _application_factory
    if _application_factory is None:
        _application_factory = ApplicationFactory()
    return _application_factory
