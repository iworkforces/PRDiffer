"""Factory for creating FastMCPServer with all dependencies properly injected."""

from prdiffer.domain.interfaces.pr_diff_reader import SessionPRDiffReader
from prdiffer.domain.interfaces.protocols import GitLabPROperationsProtocol
from prdiffer.application.provider_resolver import create_provider_capability_resolver
from prdiffer.application.startup_config import resolve_mcp_server_config
from prdiffer.domain.config.mcp_server_config import MCPServerConfig

from .mcp_server import FastMCPServer
from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from typing import Any, TypeGuard

from prdiffer.infrastructure.factories.infrastructure_factory import get_infrastructure_factory
from prdiffer.application.factories.application_factory import get_application_factory


def _is_gitlab_pr_operations(value: object) -> TypeGuard[GitLabPROperationsProtocol]:
    """Structural check: object exposes GitLab approve + description methods."""
    approve = getattr(value, "approve_pr_with_comment", None)
    describe = getattr(value, "update_pr_description", None)
    return callable(approve) and callable(describe)


def _resolve_gitlab_pr_operations(
    gitlab_reader: SessionPRDiffReader | None,
    explicit_operations: GitLabPROperationsProtocol | None,
) -> GitLabPROperationsProtocol | None:
    if explicit_operations is not None:
        return explicit_operations
    if gitlab_reader is not None and _is_gitlab_pr_operations(gitlab_reader):
        return gitlab_reader
    return None


def create_mcp_server(
    github_repository_class: type[Any],
    settings_service: SettingsServiceInterface | None = None,
    cache_service: CacheServiceInterface | None = None,
    pr_diff_service: SessionPRDiffReader | None = None,
    gitlab_reader: SessionPRDiffReader | None = None,
    gitlab_pr_operations: GitLabPROperationsProtocol | None = None,
    logger: LoggerServiceInterface | None = None,
    mcp_config: MCPServerConfig | None = None,
) -> FastMCPServer:
    """Create FastMCPServer with all dependencies properly injected."""
    infrastructure_factory = get_infrastructure_factory()
    application_factory = get_application_factory()

    if settings_service is None:
        settings_service = infrastructure_factory.create_settings_service()

    if mcp_config is None:
        mcp_config = resolve_mcp_server_config(settings_service)

    if logger is None:
        logger = infrastructure_factory.create_logger_service(transport=mcp_config.transport)

    if cache_service is None:
        cache_service = infrastructure_factory.create_cache_service()

    rate_limiter = application_factory.create_rate_limiter(logger)
    metrics_tracker = application_factory.create_metrics_tracker(logger)
    server_configuration = application_factory.create_server_configuration(settings_service, logger, mcp_config=mcp_config)
    authentication = application_factory.create_authentication(logger)

    health_monitor = application_factory.create_health_monitor(
        metrics_tracker=metrics_tracker,
        rate_limiter=rate_limiter,
        logger=logger,
    )

    if pr_diff_service is None:
        pr_diff_service = infrastructure_factory.create_pr_diff_service()
    if not isinstance(pr_diff_service, SessionPRDiffReader):
        raise TypeError("MCP strict diff routing requires a session-capable GitHub reader")

    input_validator_instance = infrastructure_factory.create_input_validator()
    from prdiffer.infrastructure.utils.coalescing_service import get_request_coalescing_service

    request_coalescing_instance = get_request_coalescing_service()

    # GitLabVCSRepository implements both SessionPRDiffReader and MR ops; reuse when
    # callers pass a single adapter and omit the dedicated operations dependency.
    resolved_gitlab_ops = _resolve_gitlab_pr_operations(gitlab_reader, gitlab_pr_operations)

    provider_resolver = create_provider_capability_resolver(
        github_reader=pr_diff_service,
        github_repository_factory=github_repository_class,
        gitlab_reader=gitlab_reader,
        gitlab_operations=resolved_gitlab_ops,
    )

    return FastMCPServer(
        settings_service=settings_service,
        cache_service=cache_service,
        provider_resolver=provider_resolver,
        logger=logger,
        rate_limiter=rate_limiter,
        metrics_tracker=metrics_tracker,
        health_monitor=health_monitor,
        server_configuration=server_configuration,
        authentication=authentication,
        input_validator=input_validator_instance,
        request_coalescing_service=request_coalescing_instance,
        mcp_config=mcp_config,
    )
