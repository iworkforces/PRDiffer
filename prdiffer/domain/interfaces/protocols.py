"""Protocol definitions for MCP server components.

This module defines the interfaces that each component must implement,
ensuring loose coupling and enabling easy testing with mocks.

These protocols are placed in the domain layer to maintain Clean Architecture
principles - the domain layer should not depend on the application layer.
"""

from typing import Any, Protocol


class RateLimiterProtocol(Protocol):
    """Protocol for rate limiting functionality."""

    def check_rate_limit(self, identifier: str) -> bool:
        """Check if the request is within rate limits.

        Args:
            identifier: Unique identifier for rate limiting (e.g., IP, user ID)

        Returns:
            True if request is allowed, False if rate limited
        """
        ...

    def increment_rate_limit(self, identifier: str) -> None:
        """Increment the rate limit counter for the identifier.

        Args:
            identifier: Unique identifier for rate limiting
        """
        ...

    def get_rate_limit_info(self) -> dict[str, Any]:
        """Get rate limit configuration and current status.

        Returns:
            Dictionary with rate limit information
        """
        ...


class MetricsTrackerProtocol(Protocol):
    """Protocol for tracking metrics and request statistics."""

    def track_request(self, operation: str, success: bool, execution_time: float) -> None:
        """Track a request for metrics collection.

        Args:
            operation: Name of the operation being tracked
            success: Whether the operation was successful
            execution_time: Time taken to execute the operation in seconds
        """
        ...

    def get_metrics_summary(self) -> dict[str, Any]:
        """Get a summary of collected metrics.

        Returns:
            Dictionary containing metrics data
        """
        ...

    def generate_request_id(self) -> str:
        """Generate a unique request ID for tracking purposes.

        Returns:
            Unique request ID
        """
        ...


class GitLabPROperationsProtocol(Protocol):
    """Provider-side GitLab MR approve / description operations for MCP tools."""

    async def approve_pr_with_comment(
        self,
        owner: str,
        repo: str,
        pr: int,
        compliment: str,
        /,
        *,
        base_url: str | None = None,
    ) -> str:
        """Approve an MR and attach a non-empty compliment note."""
        ...

    async def update_pr_description(
        self,
        owner: str,
        repo: str,
        pr: int,
        description: str,
        /,
        *,
        base_url: str | None = None,
    ) -> str:
        """Update an MR description with non-empty text."""
        ...


class HealthMonitorProtocol(Protocol):
    """Protocol for health monitoring and status checks."""

    def check_health(self) -> dict[str, Any]:
        """Perform health check and return status.

        Returns:
            Dictionary containing health status information
        """
        ...


class ServerConfigurationProtocol(Protocol):
    """Protocol for server configuration and setup."""

    def setup_logging(self) -> None:
        """Set up logging configuration."""
        ...

    def get_server_info(self) -> dict[str, Any]:
        """Get server information and configuration.

        Returns:
            Dictionary containing server information including:
            - name: Server name
            - version: Server version
            - description: Server description
            - transport: Transport mode (stdio, http, sse)
            - port: Server port
            - host: Server host
            - environment: Environment (development, production)
            - features: Enabled features dictionary
        """
        ...

    def get_mcp_instructions(self) -> str:
        """Get MCP server instructions for clients.

        Returns:
            String containing MCP server instructions
        """
        ...


class AuthenticationProtocol(Protocol):
    """Protocol for authentication and authorization."""

    def authenticate(self, api_key: str | None, *, source: str) -> tuple[bool, str | None]:
        """Authenticate a request using API key.

        Args:
            api_key: The API key to validate (may be None for unauthenticated requests)
            source: Trusted transport identity for source-scoped failure tracking

        Returns:
            Tuple of (is_authenticated, client_id) where:
            - is_authenticated: True if authentication succeeded
            - client_id: Client identifier for rate limiting (None if not authenticated)
        """
        ...

    def extract_client_identifier(self, headers: dict[str, str]) -> tuple[str | None, str | None]:
        """Extract client identifier from request headers.

        Extracts API keys from X-API-Key or Authorization (Bearer) headers.
        Forwarded IP headers are not trusted identities.

        Args:
            headers: Request headers dictionary

        Returns:
            Tuple of (api_key, client_id) where:
            - api_key: The extracted API key (or None if not present)
            - client_id: None; supplied by transport context or authentication
        """
        ...

    def is_authentication_enabled(self) -> bool:
        """Check if authentication is enabled.

        Returns:
            True if authentication is required, False otherwise.
            Status is determined by MCP_AUTH_ENABLED environment variable.
        """
        ...

    def get_status(self) -> dict[str, Any]:
        """Get authentication status and configuration.

        Returns:
            Dictionary containing authentication status
        """
        ...
