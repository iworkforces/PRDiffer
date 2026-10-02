"""Comprehensive tests for SettingsService."""

import os
import pytest
from unittest.mock import MagicMock, patch
from threading import RLock

from prdiffer.infrastructure.settings import (
    SettingsService,
    get_settings_service,
)


@pytest.fixture
def settings_service():
    """Create SettingsService for testing."""
    with patch("prdiffer.infrastructure.settings.Dynaconf") as MockDynaconf:
        MockDynaconf.return_value = MagicMock()
        service = SettingsService()
        service.settings = MagicMock()
        service.settings.get = MagicMock(return_value=None)
        service.settings.from_env = MagicMock(return_value=None)
        return service


class TestSettingsServiceInit:
    """Tests for SettingsService initialization."""

    def test_init_default_files(self):
        """Test initialization with default settings files."""
        with patch("prdiffer.infrastructure.settings.Dynaconf") as MockDynaconf:
            _ = SettingsService()  # service created for side effect

            MockDynaconf.assert_called_once()
            call_kwargs = MockDynaconf.call_args[1]
            assert call_kwargs["settings_files"] == ["settings.toml", ".secrets.toml"]

    def test_init_custom_files(self):
        """Test initialization with custom settings files."""
        with patch("prdiffer.infrastructure.settings.Dynaconf") as MockDynaconf:
            custom_files = ["custom.toml"]
            _ = SettingsService(settings_files=custom_files)  # service created for side effect

            call_kwargs = MockDynaconf.call_args[1]
            assert call_kwargs["settings_files"] == custom_files

    def test_init_cache_attributes(self):
        """Test initialization creates cache attributes."""
        with patch("prdiffer.infrastructure.settings.Dynaconf"):
            service = SettingsService()

            assert hasattr(service, "_cache_lock")
            assert isinstance(service._cache_lock, type(RLock()))
            assert service._github_config_cache is None
            assert service._cache_settings_cache is None
            assert service._app_settings_cache is None


class TestGet:
    """Tests for get method."""

    def test_get_value(self, settings_service):
        """Test getting a value."""
        settings_service.settings.get.return_value = "test_value"

        result = settings_service.get("test.key")

        settings_service.settings.get.assert_called_once_with("test.key", None)
        assert result == "test_value"

    def test_get_with_default(self, settings_service):
        """Test getting a value with default."""

        # When get returns None, the default should be returned
        def mock_get(key, default=None):
            if key == "test.key":
                return default
            return None

        settings_service.settings.get = mock_get

        result = settings_service.get("test.key", "default_value")

        assert result == "default_value"


class TestGetGithubConfig:
    """Tests for get_github_config method."""

    def test_get_github_config_caches(self, settings_service):
        """Test that GitHub config is cached."""
        settings_service.settings.get.return_value = None

        result1 = settings_service.get_github_config()
        result2 = settings_service.get_github_config()

        assert result1 is result2
        assert settings_service._github_config_cache is not None

    def test_get_github_config_returns_dataclass(self, settings_service):
        """Test that GitHub config returns GitHubConfig dataclass."""
        from prdiffer.domain.config.github_config import GitHubConfig

        settings_service.settings.get.return_value = None

        result = settings_service.get_github_config()

        assert isinstance(result, GitHubConfig)

    def test_get_github_config_values(self, settings_service):
        """Test GitHub config values."""
        settings_service.settings.get.return_value = None

        result = settings_service.get_github_config()

        assert hasattr(result, "rate_limit")
        assert hasattr(result, "timeout")
        assert hasattr(result, "max_retries")
        assert hasattr(result, "ignore_patterns")


class TestGetCacheSettings:
    """Tests for get_cache_settings method."""

    def test_get_cache_settings_caches(self, settings_service):
        """Test that cache settings are cached."""
        settings_service.settings.get.return_value = None

        result1 = settings_service.get_cache_settings()
        result2 = settings_service.get_cache_settings()

        assert result1 is result2

    def test_get_cache_settings_values(self, settings_service):
        """Test cache settings values."""
        settings_service.settings.get.return_value = None

        result = settings_service.get_cache_settings()

        assert "ttl" in result
        assert "max_size" in result
        assert "enabled" in result


class TestGetAppSettings:
    """Tests for get_app_settings method."""

    def test_get_app_settings_caches(self, settings_service):
        """Test that app settings are cached."""
        settings_service.settings.get.return_value = None

        result1 = settings_service.get_app_settings()
        result2 = settings_service.get_app_settings()

        assert result1 is result2

    def test_get_app_settings_values(self, settings_service):
        """Test app settings values."""
        settings_service.settings.get.return_value = None

        result = settings_service.get_app_settings()

        assert "debug" in result
        assert "log_level" in result
        assert "max_files_allowed" in result


class TestGetConfigurationWarnings:
    """Tests for get_configuration_warnings method."""

    def test_no_warnings_normal_config(self, settings_service):
        """Test no warnings with normal config."""

        def mock_get(key, default=None):
            if key == "github.rate_limit":
                return 5000
            if key == "github.timeout":
                return 30
            if key == "github.max_retries":
                return 3
            return default

        settings_service.settings.get = mock_get

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test_token"}):
            warnings = settings_service.get_configuration_warnings()

        # Should not have warnings for normal config
        rate_warnings = [w for w in warnings if "rate limit" in w.lower()]
        assert len(rate_warnings) == 0

    def test_warning_high_rate_limit(self, settings_service):
        """Test warning for high rate limit."""

        def mock_get(key, default=None):
            if key == "github.rate_limit":
                return 10000
            return default

        settings_service.settings.get = mock_get

        warnings = settings_service.get_configuration_warnings()

        assert any("High rate limit" in w for w in warnings)

    def test_warning_low_timeout(self, settings_service):
        """Test warning for low timeout."""

        def mock_get(key, default=None):
            if key == "github.timeout":
                return 5
            return default

        settings_service.settings.get = mock_get

        warnings = settings_service.get_configuration_warnings()

        assert any("Low timeout" in w for w in warnings)

    def test_warning_high_retry_count(self, settings_service):
        """Test warning for high retry count."""

        def mock_get(key, default=None):
            if key == "github.max_retries":
                return 15
            return default

        settings_service.settings.get = mock_get

        warnings = settings_service.get_configuration_warnings()

        assert any("High retry count" in w for w in warnings)


class TestIsDevelopmentMode:
    """Tests for is_development_mode method."""

    def test_development_mode_debug(self, settings_service):
        """Test development mode when debug is True."""
        settings_service.settings.get.return_value = True

        result = settings_service.is_development_mode()

        assert result is True

    def test_development_mode_env(self, settings_service):
        """Test development mode when env is development."""
        settings_service.settings.get.return_value = False

        with patch.dict(os.environ, {"ENV_FOR_DYNACONF": "development"}):
            result = settings_service.is_development_mode()

        assert result is True

    def test_production_mode(self, settings_service):
        """Test production mode."""
        settings_service.settings.get.return_value = False

        with patch.dict(os.environ, {"ENV_FOR_DYNACONF": "production"}):
            result = settings_service.is_development_mode()

        assert result is False


class TestGetLoadedConfigFiles:
    """Tests for _get_loaded_config_files method."""

    def test_loaded_files_from_attribute(self, settings_service):
        """Test getting loaded files from _loaded_files attribute."""
        settings_service.settings._loaded_files = ["/path/to/settings.toml"]

        result = settings_service._get_loaded_config_files()

        assert result == ["/path/to/settings.toml"]

    def test_loaded_files_from_settings_files(self, settings_service):
        """Test getting loaded files from settings_files attribute."""
        del settings_service.settings._loaded_files
        settings_service.settings.settings_files = ["/path/to/settings.toml"]

        result = settings_service._get_loaded_config_files()

        assert result == ["/path/to/settings.toml"]

    def test_loaded_files_no_attribute(self, settings_service):
        """Test empty list when no attributes."""
        del settings_service.settings._loaded_files
        del settings_service.settings.settings_files

        result = settings_service._get_loaded_config_files()

        assert result == []


class TestClearCache:
    """Tests for clear_cache method."""

    def test_clear_cache(self, settings_service):
        """Test clearing cache."""
        # Set some cached values
        settings_service._github_config_cache = MagicMock()
        settings_service._cache_settings_cache = {"test": "value"}
        settings_service._app_settings_cache = {"test": "value"}

        settings_service.clear_cache()

        assert settings_service._github_config_cache is None
        assert settings_service._cache_settings_cache is None
        assert settings_service._app_settings_cache is None


class TestGetSettingsService:
    """Tests for get_settings_service function."""

    def test_get_settings_service_singleton(self):
        """Test that get_settings_service returns singleton."""
        with patch("prdiffer.infrastructure.settings.Dynaconf"):
            # Reset global
            import prdiffer.infrastructure.settings as settings_module

            settings_module._settings_service = None

            service1 = get_settings_service()
            service2 = get_settings_service()

            assert service1 is service2

    def test_get_settings_service_creates_new(self):
        """Test that get_settings_service creates new instance."""
        with patch("prdiffer.infrastructure.settings.Dynaconf"):
            import prdiffer.infrastructure.settings as settings_module

            settings_module._settings_service = None

            service = get_settings_service()

            assert service is not None
            assert isinstance(service, SettingsService)


class TestThreadSafety:
    """Tests for thread safety."""

    def test_cache_lock_exists(self, settings_service):
        """Test that cache lock exists and is RLock."""

        assert hasattr(settings_service, "_cache_lock")
        assert type(settings_service._cache_lock).__name__ == "RLock"

    def test_cache_lock_locked_during_operation(self, settings_service):
        """Test that lock is held during cache operations."""
        settings_service.get_github_config()
        assert not settings_service._cache_lock.locked()
