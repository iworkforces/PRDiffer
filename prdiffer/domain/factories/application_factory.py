"""Application factory interface defining contracts for application-layer component creation."""

from abc import ABC, abstractmethod

from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.services.settings import SettingsServiceInterface

from prdiffer.domain.interfaces.protocols import (
    RateLimiterProtocol,
    MetricsTrackerProtocol,
    HealthMonitorProtocol,
    ServerConfigurationProtocol,
    AuthenticationProtocol,
)


class ApplicationFactoryInterface(ABC):
    """Abstract factory for creating application-layer components (rate limiting, metrics, auth, health)."""

    @abstractmethod
    def create_rate_limiter(self, logger: LoggerServiceInterface) -> RateLimiterProtocol:
        pass

    @abstractmethod
    def create_metrics_tracker(self, logger: LoggerServiceInterface) -> MetricsTrackerProtocol:
        pass

    @abstractmethod
    def create_health_monitor(
        self,
        metrics_tracker: MetricsTrackerProtocol,
        rate_limiter: RateLimiterProtocol,
        logger: LoggerServiceInterface,
    ) -> HealthMonitorProtocol:
        pass

    @abstractmethod
    def create_server_configuration(
        self,
        settings_service: SettingsServiceInterface,
        logger: LoggerServiceInterface,
    ) -> ServerConfigurationProtocol:
        pass

    @abstractmethod
    def create_authentication(self, logger: LoggerServiceInterface) -> AuthenticationProtocol:
        pass
