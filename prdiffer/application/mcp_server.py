from fastmcp import FastMCP

from prdiffer.application.provider_resolver import ProviderCapabilityResolver
from prdiffer.version import __version__

from prdiffer.domain.config.mcp_server_config import MCPServerConfig
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
        input_validator: InputValidatorProtocol,
        request_coalescing_service: RequestCoalescingProtocol,
        mcp_config: MCPServerConfig,
        authentication: AuthenticationProtocol | None = None,
    ):
        self._settings_service = settings_service
        self._mcp_config = mcp_config
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

        self._input_validator = input_validator
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
        """Start using only the immutable configuration resolved before initialization."""
        config = self._mcp_config
        if config.is_stdio:
            self._logger.info("Running MCP server with stdio transport")
            self.mcp.run(transport="stdio")
        else:
            self._logger.info(f"Running MCP server with {config.transport} transport on {config.host}:{config.port}{config.path}")
            self.mcp.run(transport=config.transport, port=config.port, host=config.host, path=config.path, uvicorn_config={"proxy_headers": False})
