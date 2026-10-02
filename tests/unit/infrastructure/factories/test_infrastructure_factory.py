"""Unit tests for InfrastructureFactory layer isolation.

Tests verify:
- No application-layer imports at the top level of infrastructure_factory.py
- All service creation methods return correct interface types
"""

import ast

from prdiffer.infrastructure.factories.infrastructure_factory import (
    InfrastructureFactory,
)
from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.diff import DiffServiceInterface
from prdiffer.domain.services.pattern_matching import (
    PatternMatchingServiceInterface,
)


class TestInfrastructureFactoryLayerIsolation:
    """Test suite for infrastructure factory layer isolation."""

    def test_no_application_imports_at_top_level(self):
        """Test that infrastructure_factory.py has NO top-level imports from prdiffer.application."""
        # Parse the infrastructure_factory.py file
        with open("prdiffer/infrastructure/factories/infrastructure_factory.py", "r") as f:
            tree = ast.parse(f.read())

        # Collect all top-level imports
        application_imports = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # Check if it's an import from prdiffer.application
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("prdiffer.application"):
                        application_imports.append(f"from {node.module} import {[alias.name for alias in node.names]}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("prdiffer.application"):
                            application_imports.append(f"import {alias.name}")

        # Assert that NO application-layer imports exist at top level
        assert len(application_imports) == 0, f"Found top-level application-layer imports: {application_imports}"

    def test_creates_settings_service(self):
        """Test that create_settings_service returns SettingsServiceInterface."""
        factory = InfrastructureFactory()
        settings_service = factory.create_settings_service()

        assert isinstance(settings_service, SettingsServiceInterface)
        assert settings_service is not None

    def test_creates_logger_service(self):
        """Test that create_logger_service returns LoggerServiceInterface."""
        factory = InfrastructureFactory()
        logger_service = factory.create_logger_service()

        assert isinstance(logger_service, LoggerServiceInterface)
        assert logger_service is not None

    def test_creates_cache_service(self):
        """Test that create_cache_service returns CacheServiceInterface."""
        factory = InfrastructureFactory()
        cache_service = factory.create_cache_service()

        assert isinstance(cache_service, CacheServiceInterface)
        assert cache_service is not None

    def test_creates_diff_service(self):
        """Test that create_diff_service returns DiffServiceInterface."""
        factory = InfrastructureFactory()
        diff_service = factory.create_diff_service()

        assert isinstance(diff_service, DiffServiceInterface)
        assert diff_service is not None

    def test_creates_pattern_matching_service(self):
        """Test that create_pattern_matching_service returns PatternMatchingServiceInterface."""
        factory = InfrastructureFactory()
        pattern_matching_service = factory.create_pattern_matching_service()

        assert isinstance(pattern_matching_service, PatternMatchingServiceInterface)
        assert pattern_matching_service is not None
