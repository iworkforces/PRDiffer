import os
from typing import Literal, TypeAlias

from fastmcp import FastMCP

from prdiffer.application.provider_resolver import ProviderCapabilityResolver
from prdiffer.version import __version__

from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.interfaces.protocols import (
    RateLimiterProtocol,
    MetricsTrackerProtocol,
    HealthMonitorProtocol,
    ServerConfigurationProtocol,
    AuthenticationProtocol,
)
from prdiffer.domain.interfaces.input_validation import InputValidatorProtocol
from prdiffer.domain.interfaces.request_coalescing import RequestCoalescingProtocol

from prdiffer.application.tool_registry import ToolRegistry
from prdiffer.application.webhook_handler import WebhookHandler
from prdiffer.application.health_endpoints import HealthEndpoints


TransportMode: TypeAlias = Literal["stdio", "http", "sse", "streamable-http"]


class FastMCPServer:
    """FastMCP server for fetching GitHub PR diffs with detailed file change information."""

    def __init__(
        self,
        settings_service: SettingsServiceInterface,
        cache_service: CacheServiceInterface,
        logger: LoggerServiceInterface,
        provider_resolver: ProviderCapabilityResolver,
        rate_limiter: RateLimiterProtocol,
        metrics_tracker: MetricsTrackerProtocol,
        health_monitor: HealthMonitorProtocol,
        server_configuration: ServerConfigurationProtocol,
        authentication: AuthenticationProtocol | None = None,
        input_validator: InputValidatorProtocol | None = None,
        request_coalescing_service: RequestCoalescingProtocol | None = None,
    ):
        self._settings_service = settings_service
        self._cache_service = cache_service
        self._logger = logger
        self._provider_resolver = provider_resolver

        self._rate_limiter = rate_limiter
        self._metrics_tracker = metrics_tracker
        self._health_monitor = health_monitor
        self._server_configuration = server_configuration

        if authentication is None:
            from prdiffer.application.components.authentication import (
                AuthenticationMiddleware,
            )

            self._authentication = AuthenticationMiddleware()
        else:
            self._authentication = authentication

        if input_validator is None:
            from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory

            self._input_validator = get_infrastructure_factory().create_input_validator()
        else:
            self._input_validator = input_validator

        if request_coalescing_service is None:
            from prdiffer.infrastructure.utils.coalescing_service import (
                get_request_coalescing_service,
            )

            self._request_coalescing = get_request_coalescing_service()
        else:
            self._request_coalescing = request_coalescing_service

        self._server_configuration.setup_logging()

        self._logger.info("Initializing FastMCP server", component="mcp_server")

        self.mcp = FastMCP(
            name="prdiffer",
            instructions=self._server_configuration.get_mcp_instructions(),
            version=__version__,
        )

        self._initialize_components()
        self._register_endpoints_and_tools()

    def _initialize_components(self) -> None:
        github_config = self._settings_service.get_github_config()

        self._tool_registry = ToolRegistry(
            cache_service=self._cache_service,
            logger=self._logger,
            provider_resolver=self._provider_resolver,
            rate_limiter=self._rate_limiter,
            metrics_tracker=self._metrics_tracker,
            authentication=self._authentication,
            input_validator=self._input_validator,
            request_coalescing_service=self._request_coalescing,
            pr_diff_request_timeout_seconds=github_config.pr_diff_request_timeout_seconds,
        )

        self._webhook_handler = WebhookHandler(
            settings_service=self._settings_service,
            cache_service=self._cache_service,
            logger=self._logger,
            input_validator=self._input_validator,
        )

        self._health_endpoints = HealthEndpoints(
            health_monitor=self._health_monitor,
            metrics_tracker=self._metrics_tracker,
            cache_service=self._cache_service,
            authentication=self._authentication,
            request_coalescing=self._request_coalescing,
            logger=self._logger,
        )

    def _register_endpoints_and_tools(self) -> None:

        # Register tools (get_pr_diff, approve_pr, describe_pr)
        self._tool_registry.register_tools(self.mcp)

        health_tool = self._health_endpoints.get_health_handler()
        self.mcp.tool()(health_tool)

        metrics_handler = self._health_endpoints.get_metrics_handler()
        self.mcp.custom_route("/metrics", methods=["GET"])(metrics_handler)

        webhook_handler_func = self._webhook_handler.get_webhook_handler()
        self.mcp.custom_route("/webhook", methods=["POST"])(webhook_handler_func)

    def run(self) -> None:
        """Start the FastMCP server with configured transport and port.

        Configuration priority (highest to lowest):
        1. Environment variables (MCP_TRANSPORT, MCP_PORT, MCP_HOST, MCP_PATH)
        2. Settings file (settings.toml)
        3. Defaults (http transport, port 9102, host 127.0.0.1, path /mcp)

        Supported transports include "stdio", "http", "sse", and "streamable-http".
        """

        # Get MCP settings from environment variables first, then fall back to settings service
        transport_raw = os.getenv("MCP_TRANSPORT") or self._settings_service.get("mcp.transport", "http")
        port = int(os.getenv("MCP_PORT", "0")) or self._settings_service.get("mcp.port", 9102)
        host = os.getenv("MCP_HOST") or self._settings_service.get("mcp.host", "127.0.0.1")
        path = os.getenv("MCP_PATH") or self._settings_service.get("mcp.path", "/mcp")

        valid_transports: tuple[TransportMode, ...] = (
            "stdio",
            "http",
            "sse",
            "streamable-http",
        )
        if transport_raw not in valid_transports:
            self._logger.warning(f"Invalid transport '{transport_raw}', defaulting to 'stdio'")
            transport: TransportMode = "stdio"
        else:
            transport = transport_raw

        if transport == "stdio":
            self._logger.info("Running MCP server with stdio transport")
            self.mcp.run(transport="stdio")
        else:
            self._logger.info(f"Running MCP server with {transport} transport on {host}:{port}{path}")
            self.mcp.run(transport=transport, port=port, host=host, path=path)
