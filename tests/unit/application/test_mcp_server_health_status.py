from unittest.mock import Mock, patch

import anyio

from prdiffer.application.mcp_server import FastMCPServer
from prdiffer.domain.config.mcp_server_config import MCPServerConfig
from prdiffer.application.provider_resolver import ProviderCapabilityResolver


class DummyCoalescingService:
    async def get_stats(self):
        return {"pending_count": 0, "pending_keys": [], "total_waiters": 0}


def test_health_status_includes_cache_and_coalescing():
    settings_service = Mock()
    cache_service = Mock()
    cache_service.get_stats.return_value = {"cache_size": 1, "keys": ["private-repository"]}

    logger = Mock()
    rate_limiter = Mock()
    metrics_tracker = Mock()
    health_monitor = Mock()
    health_monitor.check_health.return_value = {"status": "healthy"}
    server_configuration = Mock()
    server_configuration.setup_logging = Mock()
    server_configuration.get_mcp_instructions = Mock(return_value="")
    authentication = Mock()
    authentication.get_status.return_value = {"authentication_enabled": False}

    request_coalescing_service = DummyCoalescingService()

    with patch("prdiffer.application.mcp_server.FastMCP"):
        server = FastMCPServer(
            mcp_config=MCPServerConfig(transport="http", host="127.0.0.1", port=9102, path="/mcp"),
            settings_service=settings_service,
            cache_service=cache_service,
            logger=logger,
            provider_resolver=ProviderCapabilityResolver(),
            rate_limiter=rate_limiter,
            metrics_tracker=metrics_tracker,
            health_monitor=health_monitor,
            server_configuration=server_configuration,
            authentication=authentication,
            input_validator=Mock(),
            request_coalescing_service=request_coalescing_service,
        )

    health = anyio.run(server._health_endpoints._get_health_status)

    assert health["cache"] == {"cache_size": 1}
    assert "repository_cache" not in health
    assert health["request_coalescing"] == {"pending_count": 0, "total_waiters": 0}
    assert health["authentication"]["authentication_enabled"] is False
