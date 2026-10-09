"""Unit tests for ServerConfiguration.

Tests ServerConfiguration which handles server configuration,
setup logging, provides server information, and validates configuration.
"""

import pytest
import logging
from unittest.mock import Mock
from typing import Any, Optional

from prdiffer.application.components.server_configuration import (
    ServerConfiguration,
    ValidationResult,
)
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.config.mcp_server_config import MCPServerConfig
from prdiffer.domain.exceptions import ConfigurationError
from prdiffer.domain.error_codes import E5009_CONFIGURATION_ERROR


HTTP_CONFIG = MCPServerConfig(transport="http", host="127.0.0.1", port=9102, path="/mcp")


class MockLogger(LoggerServiceInterface):
    """Mock logger for testing."""

    def debug(self, message: str, **kwargs) -> None:
        pass

    def info(self, message: str, **kwargs) -> None:
        pass

    def warning(self, message: str, **kwargs) -> None:
        pass

    def error(self, message: str, **kwargs) -> None:
        pass

    def critical(self, message: str, **kwargs) -> None:
        pass

    def should_log(self, level) -> bool:
        return True


class TestServerConfigurationInitialization:
    """Test suite for ServerConfiguration initialization."""

    def test_server_configuration_initialization(self):
        """Test that ServerConfiguration can be initialized."""
        logger = MockLogger()

        config = ServerConfiguration(None, logger, mcp_config=HTTP_CONFIG)

        assert config is not None
        assert hasattr(config, "_settings_service")


class TestServerConfigurationGetMcpInstructions:
    """Test suite for get_mcp_instructions method."""

    def test_get_mcp_instructions_returns_string(self):
        """Test that get_mcp_instructions returns a string."""
        logger = MockLogger()

        config = ServerConfiguration(None, logger, mcp_config=HTTP_CONFIG)
        instructions = config.get_mcp_instructions()

        assert isinstance(instructions, str)
        assert len(instructions) > 0

    def test_get_mcp_instructions_contains_tool_info(self):
        """Test that instructions contain tool information."""
        logger = MockLogger()

        config = ServerConfiguration(None, logger, mcp_config=HTTP_CONFIG)
        instructions = config.get_mcp_instructions()

        assert "get_pr_diff" in instructions or "pr_diff" in instructions


class TestValidationResult:
    """Test suite for ValidationResult TypedDict."""

    def test_validation_result_typeddict_properties(self):
        """Test that ValidationResult has correct properties."""
        result = ValidationResult(
            valid=True,
            warnings=["warning1", "warning2"],
            errors=["error1"],
        )

        assert result["valid"] is True
        assert result["warnings"] == ["warning1", "warning2"]
        assert result["errors"] == ["error1"]


class MockSettingsService(SettingsServiceInterface):
    """Mock settings service for testing."""

    def __init__(self, settings: Optional[dict[str, Any]] = None) -> None:
        """Initialize mock settings with default values."""
        self._settings = settings or {}

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        keys = key.split(".")
        value = self._settings
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def get_cache_settings(self) -> dict[str, Any]:
        """Get cache-related settings."""
        return {}

    def get_app_settings(self) -> dict[str, Any]:
        """Get application-specific settings."""
        return {}

    def clear_cache(self) -> None:
        """Clear all cached settings."""
        pass


@pytest.mark.unit
class TestSetupLogging:
    """Test suite for setup_logging method."""

    def test_setup_logging_with_debug_level(self):
        """Test setup_logging with DEBUG log level."""
        settings = MockSettingsService({"app": {"log_level": "debug"}})
        logger = Mock(spec=LoggerServiceInterface)
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        config.setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.DEBUG
        logger.info.assert_called_once()

    def test_setup_logging_with_info_level(self):
        """Test setup_logging with INFO log level."""
        settings = MockSettingsService({"app": {"log_level": "INFO"}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        config.setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO

    def test_setup_logging_with_warning_level(self):
        """Test setup_logging with WARNING log level."""
        settings = MockSettingsService({"app": {"log_level": "warning"}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        config.setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.WARNING

    def test_setup_logging_with_error_level(self):
        """Test setup_logging with ERROR log level."""
        settings = MockSettingsService({"app": {"log_level": "ERROR"}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        config.setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.ERROR

    def test_setup_logging_with_critical_level(self):
        """Test setup_logging with CRITICAL log level."""
        settings = MockSettingsService({"app": {"log_level": "critical"}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        config.setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.CRITICAL

    def test_setup_logging_with_default_level(self):
        """Test setup_logging with default (INFO) log level."""
        settings = MockSettingsService({})  # No log_level configured
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        config.setup_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO

    def test_setup_logging_with_invalid_level(self):
        """Test setup_logging with invalid log level (should not crash)."""
        settings = MockSettingsService({"app": {"log_level": "INVALID"}})
        logger = Mock(spec=LoggerServiceInterface)
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        config.setup_logging()

        logger.info.assert_called_once()
        logger.error.assert_not_called()

    def test_setup_logging_handles_exception(self):
        """Test setup_logging handles exceptions gracefully."""
        settings = Mock(spec=SettingsServiceInterface)
        settings.get.side_effect = Exception("Settings service error")
        logger = Mock(spec=LoggerServiceInterface)
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        # Should not raise exception
        config.setup_logging()

        logger.error.assert_called_once()


@pytest.mark.unit
class TestGetServerInfo:
    """Test suite for get_server_info method."""

    def test_get_server_info_returns_all_expected_fields(self):
        """Test get_server_info returns all expected fields."""
        settings = MockSettingsService(
            {
                "mcp": {
                    "transport": "http",
                    "port": 9102,
                    "host": "0.0.0.0",
                    "path": "/mcp",
                },
                "env": "production",
                "debug": False,
            }
        )
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=MCPServerConfig(transport="http", host="0.0.0.0", port=9102, path="/mcp"))

        info = config.get_server_info()

        assert info["name"] == "prdiffer"
        assert "version" in info
        assert info["description"] == "GitHub PR Diff Fetcher MCP Server"
        assert info["transport"] == "http"
        assert info["port"] == 9102
        assert info["host"] == "0.0.0.0"
        assert info["path"] == "/mcp"
        assert info["environment"] == "production"
        assert info["debug_mode"] is False
        assert "features" in info
        assert isinstance(info["features"], dict)
        assert info["features"]["caching"] is True
        assert info["features"]["rate_limiting"] is True

    def test_get_server_info_with_default_values(self):
        """Test get_server_info returns default values when not configured."""
        settings = MockSettingsService({})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        info = config.get_server_info()

        assert info["transport"] == "http"
        assert info["port"] == 9102
        assert info["host"] == "127.0.0.1"
        assert info["path"] == "/mcp"

    def test_get_server_info_handles_exception(self):
        """Test get_server_info handles exceptions gracefully."""
        settings = Mock(spec=SettingsServiceInterface)
        settings.get.side_effect = Exception("Settings service error")
        logger = Mock(spec=LoggerServiceInterface)
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        info = config.get_server_info()

        assert info["name"] == "prdiffer"
        assert info["version"] == "unknown"
        assert info["description"] == "GitHub PR Diff Fetcher MCP Server"
        assert "error" in info
        logger.error.assert_called_once()


@pytest.mark.unit
class TestValidateConfiguration:
    """Test suite for validate_configuration method."""

    def test_validate_configuration_with_valid_http_transport(self):
        """Test validate_configuration with valid http transport."""
        settings = MockSettingsService({"mcp": {"transport": "http", "port": 9102}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        result = config.validate_configuration()

        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_validate_configuration_with_valid_sse_transport(self):
        """Test validate_configuration with valid sse transport."""
        settings = MockSettingsService({"mcp": {"transport": "sse", "port": 8080}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=MCPServerConfig(transport="sse", host="127.0.0.1", port=8080, path="/mcp"))

        result = config.validate_configuration()

        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_validate_configuration_with_valid_stdio_transport(self):
        """Test validate_configuration with valid stdio transport."""
        settings = MockSettingsService({"mcp": {"transport": "stdio"}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=MCPServerConfig(transport="stdio", host="127.0.0.1", port=None, path="/mcp"))

        result = config.validate_configuration()

        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_validate_configuration_with_unknown_transport(self):
        """Unknown transports cannot reach the server configuration component."""
        with pytest.raises(ConfigurationError) as raised:
            MCPServerConfig(transport="unknown", host="127.0.0.1", port=9102, path="/mcp")
        assert raised.value.error_code == E5009_CONFIGURATION_ERROR

    def test_validate_configuration_with_valid_port(self):
        """Test validate_configuration with valid port."""
        settings = MockSettingsService({"mcp": {"transport": "http", "port": 8080}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=MCPServerConfig(transport="http", host="127.0.0.1", port=8080, path="/mcp"))

        result = config.validate_configuration()

        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_validate_configuration_with_minimum_port(self):
        """Test validate_configuration with minimum port (1)."""
        settings = MockSettingsService({"mcp": {"transport": "http", "port": 1}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=MCPServerConfig(transport="http", host="127.0.0.1", port=1, path="/mcp"))

        result = config.validate_configuration()

        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_validate_configuration_with_maximum_port(self):
        """Test validate_configuration with maximum port (65535)."""
        settings = MockSettingsService({"mcp": {"transport": "http", "port": 65535}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=MCPServerConfig(transport="http", host="127.0.0.1", port=65535, path="/mcp"))

        result = config.validate_configuration()

        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_validate_configuration_with_zero_port(self):
        with pytest.raises(ConfigurationError) as raised:
            MCPServerConfig(transport="http", host="127.0.0.1", port=0, path="/mcp")
        assert raised.value.error_code == E5009_CONFIGURATION_ERROR

    def test_validate_configuration_with_negative_port(self):
        with pytest.raises(ConfigurationError) as raised:
            MCPServerConfig(transport="http", host="127.0.0.1", port=-1, path="/mcp")
        assert raised.value.error_code == E5009_CONFIGURATION_ERROR

    def test_validate_configuration_with_too_large_port(self):
        with pytest.raises(ConfigurationError) as raised:
            MCPServerConfig(transport="http", host="127.0.0.1", port=65536, path="/mcp")
        assert raised.value.error_code == E5009_CONFIGURATION_ERROR

    def test_validate_configuration_with_string_port(self):
        with pytest.raises(ConfigurationError) as raised:
            MCPServerConfig(transport="http", host="127.0.0.1", port="9102", path="/mcp")
        assert raised.value.error_code == E5009_CONFIGURATION_ERROR

    def test_validate_configuration_with_boolean_port(self):
        with pytest.raises(ConfigurationError) as raised:
            MCPServerConfig(transport="http", host="127.0.0.1", port=True, path="/mcp")
        assert raised.value.error_code == E5009_CONFIGURATION_ERROR

    def test_validate_configuration_with_streamable_http(self):
        config = ServerConfiguration(
            MockSettingsService({"mcp": {"transport": "unknown"}}),
            MockLogger(),
            mcp_config=MCPServerConfig(transport="streamable-http", host="127.0.0.1", port=9102, path="/mcp"),
        )
        result = config.validate_configuration()
        assert result == {"valid": True, "warnings": [], "errors": []}

    def test_validate_configuration_stdio_ignores_port(self):
        """Test validate_configuration with stdio transport ignores port validation."""
        settings = MockSettingsService({"mcp": {"transport": "stdio", "port": 99999}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=MCPServerConfig(transport="stdio", host="127.0.0.1", port=None, path="/mcp"))

        result = config.validate_configuration()

        assert result["valid"] is True
        assert len(result["errors"]) == 0

        assert config.get_server_info()["port"] is None

    def test_validate_configuration_no_github_token_warning(self):
        """Test validate_configuration warns when no GITHUB_TOKEN is set."""
        import os

        # Ensure GITHUB_TOKEN is not set
        if "GITHUB_TOKEN" in os.environ:
            del os.environ["GITHUB_TOKEN"]

        settings = MockSettingsService({"mcp": {"transport": "http", "port": 9102}})
        logger = MockLogger()
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        result = config.validate_configuration()

        assert result["valid"] is True
        assert len(result["warnings"]) == 1
        assert "No GITHUB_TOKEN environment variable set" in result["warnings"][0]

    def test_validate_configuration_handles_exception(self):
        """Validated startup configuration never re-reads transport settings."""
        settings = Mock(spec=SettingsServiceInterface)
        settings.get.side_effect = Exception("Settings service error")
        logger = Mock(spec=LoggerServiceInterface)
        config = ServerConfiguration(settings, logger, mcp_config=HTTP_CONFIG)

        result = config.validate_configuration()

        assert result == {"valid": True, "warnings": [], "errors": []}
        settings.get.assert_not_called()
        logger.error.assert_not_called()
