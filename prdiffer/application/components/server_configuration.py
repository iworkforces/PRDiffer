"""Server configuration component."""

import logging
import os

from typing import Any, TypedDict
from prdiffer.domain.config.mcp_server_config import MCPServerConfig
from prdiffer.domain.interfaces.protocols import ServerConfigurationProtocol
from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.version import __version__
from prdiffer.domain.services.logger import LoggerServiceInterface


class ValidationResult(TypedDict):
    valid: bool
    warnings: list[str]
    errors: list[str]


class ServerConfiguration(ServerConfigurationProtocol):
    """Component responsible for server configuration and setup."""

    def __init__(
        self,
        settings_service: SettingsServiceInterface,
        logger: logging.Logger | LoggerServiceInterface | None = None,
        *,
        mcp_config: MCPServerConfig,
    ):
        self._settings_service = settings_service
        self._mcp_config = mcp_config
        self._logger = logger or logging.getLogger(__name__)

    def setup_logging(self) -> None:
        """Set up logging configuration."""
        try:
            log_level = self._settings_service.get("app.log_level", "INFO").upper()

            root_logger = logging.getLogger()
            if log_level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
                root_logger.setLevel(getattr(logging, log_level))

            self._logger.info(f"Logging configuration completed with level: {log_level}")

        except Exception as e:
            self._logger.error(f"Failed to setup logging: {str(e)}")

    def get_server_info(self) -> dict[str, Any]:
        """Get server information and configuration."""
        try:
            return {
                "name": "prdiffer",
                "version": __version__,
                "description": "GitHub PR Diff Fetcher MCP Server",
                "transport": self._mcp_config.transport,
                "port": self._mcp_config.port,
                "host": self._mcp_config.host,
                "path": self._mcp_config.path,
                "environment": self._settings_service.get("env", "development"),
                "debug_mode": self._settings_service.get("debug", False),
                "features": {
                    "caching": True,
                    "rate_limiting": True,
                    "metrics_tracking": True,
                    "health_monitoring": True,
                },
            }
        except Exception as e:
            self._logger.error(f"Failed to get server info: {str(e)}")
            return {
                "name": "prdiffer",
                "version": "unknown",
                "description": "GitHub PR Diff Fetcher MCP Server",
                "error": str(e),
            }

    def get_mcp_instructions(self) -> str:
        """Get MCP server instructions for clients."""
        return """
        prdiffer MCP server - GitHub Pull Request Analysis Tools

        Available Tools:
            • get_pr_diff(pr_url) - Fetch complete GitHub PR code diff
            • health() - Get server health and metrics

        Usage: Call tools with GitHub PR URLs (e.g., "https://github.com/owner/repo/pull/123")
        """

    def validate_configuration(self) -> ValidationResult:
        """Report warnings for the already validated startup configuration."""
        validation_results: ValidationResult = {
            "valid": True,
            "warnings": [],
            "errors": [],
        }

        if not os.getenv("GITHUB_TOKEN"):
            validation_results["warnings"].append(
                "No GITHUB_TOKEN environment variable set, API rate limits may apply. Set GITHUB_TOKEN=your_token or add to .env file"
            )
        self._logger.info(f"Configuration validation completed for {self._mcp_config.transport}: {validation_results}")

        return validation_results
