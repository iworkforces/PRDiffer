"""Dependency injection container for PRDiffer."""

from collections.abc import Callable
from typing import Any, TypeVar
from threading import Lock
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.exceptions import PRDifferException, ConfigurationError
from prdiffer.domain.error_codes import E5009_CONFIGURATION_ERROR


T = TypeVar("T")


class DependencyAlreadyRegisteredError(PRDifferException):
    """Raised when attempting to register a dependency that's already registered."""

    def __init__(self, name: str):
        self.name = name
        message = f"Dependency '{name}' is already registered"
        super().__init__(message=message)


class ServiceContainer:
    """Lightweight dependency injection container.

    Supports singleton and transient service registration with thread-safe
    resolution by interface type.
    """

    _singleton_instances: dict[str, Any] = {}
    _transient_factories: dict[str, Callable[[], Any]] = {}
    _lock: Lock
    _logger: LoggerServiceInterface

    def __init__(self, logger: LoggerServiceInterface):
        self._lock = Lock()
        self._logger = logger
        self._singleton_instances: dict[str, Any] = {}
        self._transient_factories: dict[str, Callable[[], Any]] = {}

    def register_singleton(
        self,
        interface_type: type,
        factory: Callable[[], Any],
        force: bool = False,
        instance: Any | None = None,
    ) -> None:
        """Register a singleton service.

        Args:
            interface_type: Interface type to register
            factory: Factory function that creates instance
            force: Force re-registration even if exists
            instance: Optional pre-created instance (for testing)

        Raises:
            DependencyAlreadyRegisteredError: If already registered with force=False
        """
        type_name = interface_type.__name__

        with self._lock:
            if type_name in self._singleton_instances and not force:
                raise DependencyAlreadyRegisteredError(type_name)

        if instance is not None:
            self._singleton_instances[type_name] = instance
        else:
            self._singleton_instances[type_name] = factory()

        self._logger.debug(f"Registered singleton: {type_name}")

    def register_transient(
        self,
        interface_type: type,
        factory: Callable[[], Any],
    ) -> None:
        """Register a transient service.

        Args:
            interface_type: Interface type to register
            factory: Factory function that creates instances
        """
        type_name = interface_type.__name__

        self._transient_factories[type_name] = factory
        self._logger.debug(f"Registered transient factory: {type_name}")

    def get(
        self,
        interface_type: type[T],
    ) -> T:
        """Get service instance by interface type.

        Raises:
            ConfigurationError: If interface_type not registered
        """
        type_name = interface_type.__name__

        if type_name in self._singleton_instances:
            return self._singleton_instances[type_name]

        if type_name in self._transient_factories:
            factory = self._transient_factories[type_name]
            instance = factory()
            self._logger.debug(f"Created transient instance of {type_name}")
            return instance

        raise ConfigurationError(f"Service {type_name} not registered", error_code=E5009_CONFIGURATION_ERROR)

    def has(self, interface_type: type) -> bool:
        """Check if interface type is registered."""
        type_name = interface_type.__name__

        return type_name in self._singleton_instances or type_name in self._transient_factories

    def is_singleton(self, interface_type: type) -> bool:
        """Check if interface type is registered as singleton."""
        type_name = interface_type.__name__

        return type_name in self._singleton_instances and type_name not in self._transient_factories

    def create(
        self,
        interface_type: type[T],
        factory: Callable[[], T],
        instance: T | None = None,
    ) -> T:
        """Create and register a service instance.

        Args:
            interface_type: Interface type to register
            factory: Factory function that creates instance
            instance: Optional pre-created instance (for testing)
        """
        type_name = interface_type.__name__

        if instance is not None:
            self._singleton_instances[type_name] = instance
            self._logger.debug(f"Created singleton instance of {type_name}")
            return instance

        if type_name in self._transient_factories:
            factory = self._transient_factories[type_name]
        else:
            self._transient_factories[type_name] = factory

        instance = factory()
        self._logger.debug(f"Created transient instance of {type_name}")

        return instance

    def clear_all(self) -> None:
        """Clear all registered services."""
        with self._lock:
            self._singleton_instances.clear()
            self._transient_factories.clear()
            self._logger.info("Cleared all services")

    def get_instance_count(self, interface_type: type) -> int:
        """Get count of registered instances (singleton + transient)."""
        type_name = interface_type.__name__

        count = 0
        if type_name in self._singleton_instances:
            count += 1
        if type_name in self._transient_factories:
            count += 1

        return count


_container: ServiceContainer | None = None


def get_container(logger: LoggerServiceInterface | None = None) -> ServiceContainer:
    """Get or create global service container.

    Raises:
        ConfigurationError: If container not initialized and no logger provided
    """
    global _container

    if _container is None:
        if logger is None:
            raise ConfigurationError(
                "ServiceContainer not initialized. Provide logger on first call.",
                error_code=E5009_CONFIGURATION_ERROR,
            )
        _container = ServiceContainer(logger=logger)

    return _container


def register_singleton_service(interface_type: type[T], factory: Callable[[], T], instance: T | None = None) -> None:
    """Convenience function to register a singleton service."""
    container = get_container()
    container.register_singleton(interface_type, factory, instance=instance)


def register_transient_factory(interface_type: type[T], factory: Callable[[], T]) -> None:
    """Convenience function to register a transient factory."""
    container = get_container()
    container.register_transient(interface_type, factory)
