"""Tests for InfrastructureFactoryInterface contract."""

from unittest.mock import MagicMock

import pytest

from prdiffer.domain.factories.infrastructure_factory import InfrastructureFactoryInterface


class ConcreteFactory(InfrastructureFactoryInterface):
    """Minimal concrete factory for testing the ABC contract."""

    def create_settings_service(self):
        return MagicMock()

    def create_logger_service(self):
        return MagicMock()

    def create_cache_service(self):
        return MagicMock()

    def create_repository_cache_service(self):
        return MagicMock()

    def create_github_api_service(self):
        return MagicMock()

    def create_diff_service(self):
        return MagicMock()

    def create_pattern_matching_service(self):
        return MagicMock()

    def create_pr_diff_service(self):
        return MagicMock()

    def create_input_validator(self):
        return MagicMock()


@pytest.mark.unit
class TestInfrastructureFactoryInterface:
    """Test InfrastructureFactoryInterface ABC contract."""

    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            InfrastructureFactoryInterface()

    def test_concrete_factory_instantiates(self):
        factory = ConcreteFactory()
        assert isinstance(factory, InfrastructureFactoryInterface)

    def test_all_abstract_methods_present(self):
        expected_methods = [
            "create_settings_service",
            "create_logger_service",
            "create_cache_service",
            "create_repository_cache_service",
            "create_github_api_service",
            "create_diff_service",
            "create_pattern_matching_service",
            "create_pr_diff_service",
            "create_input_validator",
        ]
        for method_name in expected_methods:
            assert hasattr(InfrastructureFactoryInterface, method_name)

    def test_each_create_method_returns_value(self):
        factory = ConcreteFactory()
        assert factory.create_settings_service() is not None
        assert factory.create_logger_service() is not None
        assert factory.create_cache_service() is not None
        assert factory.create_repository_cache_service() is not None
        assert factory.create_github_api_service() is not None
        assert factory.create_diff_service() is not None
        assert factory.create_pattern_matching_service() is not None
        assert factory.create_pr_diff_service() is not None
        assert factory.create_input_validator() is not None

    def test_missing_method_raises_type_error(self):
        """Subclass missing one abstract method cannot be instantiated."""

        class IncompleteFactory(InfrastructureFactoryInterface):
            def create_settings_service(self):
                return MagicMock()

            # Missing all other methods

        with pytest.raises(TypeError):
            IncompleteFactory()
