"""Infrastructure factory interface for Clean Architecture.

This module defines the InfrastructureFactoryInterface that provides
a clean abstraction for creating infrastructure services while maintaining
dependency inversion principle.

Note: Application-layer component creation has been moved to ApplicationFactoryInterface.
"""

from abc import ABC, abstractmethod

from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.services.repository_cache import RepositoryCacheServiceInterface
from prdiffer.domain.services.github_api import GitHubAPIServiceInterface
from prdiffer.domain.services.diff import DiffServiceInterface
from prdiffer.domain.services.pattern_matching import PatternMatchingServiceInterface
from prdiffer.domain.interfaces.pr_diff_reader import SessionPRDiffReader
from prdiffer.domain.interfaces.input_validation import InputValidatorProtocol


class InfrastructureFactoryInterface(ABC):
    """Abstract factory for creating infrastructure services (not application components)."""

    @abstractmethod
    def create_settings_service(self) -> SettingsServiceInterface:
        pass

    @abstractmethod
    def create_logger_service(self) -> LoggerServiceInterface:
        pass

    @abstractmethod
    def create_cache_service(self) -> CacheServiceInterface:
        pass

    @abstractmethod
    def create_repository_cache_service(self) -> RepositoryCacheServiceInterface:
        pass

    @abstractmethod
    def create_github_api_service(self) -> GitHubAPIServiceInterface:
        pass

    @abstractmethod
    def create_diff_service(self) -> DiffServiceInterface:
        pass

    @abstractmethod
    def create_pattern_matching_service(self) -> PatternMatchingServiceInterface:
        pass

    @abstractmethod
    def create_pr_diff_service(self) -> SessionPRDiffReader:
        pass

    @abstractmethod
    def create_input_validator(self) -> InputValidatorProtocol:
        pass
