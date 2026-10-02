"""Concrete infrastructure factory implementation."""

from prdiffer.domain.factories.infrastructure_factory import (
    InfrastructureFactoryInterface,
)
from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface
from prdiffer.domain.interfaces.pr_diff_reader import SessionPRDiffReader
from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.services.repository_cache import RepositoryCacheServiceInterface
from prdiffer.domain.services.github_api import GitHubAPIServiceInterface
from prdiffer.domain.services.diff import DiffServiceInterface
from prdiffer.domain.services.pattern_matching import PatternMatchingServiceInterface
from prdiffer.domain.interfaces.input_validation import InputValidatorProtocol

from prdiffer.infrastructure.settings import get_settings_service
from prdiffer.infrastructure.logging.console_logger import get_logger
from prdiffer.infrastructure.cache.service import get_cache_service
from prdiffer.infrastructure.cache.cache_repository import (
    get_repository_cache_service,
)
from prdiffer.infrastructure.github.client import GitHubAPIClient
from prdiffer.infrastructure.utils.diff_utils import DiffUtils, DiffProcessingConfig
from prdiffer.infrastructure.utils.pattern_matcher import PatternMatcher
from prdiffer.infrastructure.github.diff_generator import (
    DiffGenerator,
    get_diff_generator,
)
from prdiffer.infrastructure.github.file_processor import FileProcessor

from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService


class InfrastructureFactory(InfrastructureFactoryInterface):
    """Concrete implementation of infrastructure factory."""

    def __init__(self) -> None:
        self._gitlab_runtime = None

    def create_settings_service(self) -> SettingsServiceInterface:
        """Create settings service instance."""
        return get_settings_service()

    def create_logger_service(self) -> LoggerServiceInterface:
        """Create logger service instance."""
        return get_logger()

    def create_cache_service(self) -> CacheServiceInterface:
        """Create cache service instance."""
        return get_cache_service()

    def create_repository_cache_service(self) -> RepositoryCacheServiceInterface:
        """Create repository cache service instance."""
        return get_repository_cache_service()

    def create_github_api_service(self) -> GitHubAPIServiceInterface:
        """Create GitHub API service instance from authoritative GitHubConfig."""
        settings_service = get_settings_service()
        config = settings_service.get_github_config()
        # Serialized capacity is 1 when parallel fetch is disabled.
        max_concurrent = config.github_worker_capacity
        return GitHubAPIClient(
            max_retries=config.max_retries,
            retry_delay=config.retry_delay,
            timeout=config.timeout,
            retry_on_404=config.retry_on_404,
            retry_on_403=config.retry_on_403,
            retry_on_500=config.retry_on_500,
            retry_log_level=config.retry_log_level,
            permanent_failure_log_level=config.permanent_failure_log_level,
            circuit_breaker_enabled=config.circuit_breaker_enabled,
            circuit_breaker_failure_threshold=config.circuit_breaker_failure_threshold,
            circuit_breaker_timeout=float(config.circuit_breaker_timeout),
            adaptive_retry_enabled=config.adaptive_retry_enabled,
            max_adaptive_delay=config.max_adaptive_delay,
            api_health_tracking=config.api_health_tracking,
            context_aware_retry=config.context_aware_retry,
            use_advanced_retry=True,
            max_concurrent=max_concurrent,
            max_file_size_bytes=config.max_file_size_bytes,
            parallel_file_fetch_enabled=config.parallel_file_fetch_enabled,
        )

    def create_diff_service(self) -> DiffServiceInterface:
        """Create diff service instance from GitHubConfig limits."""
        config = get_settings_service().get_github_config()
        processing = DiffProcessingConfig(
            large_file_threshold=config.large_file_threshold,
            chunk_size=config.chunk_size,
            max_diff_size=config.max_diff_size,
        )
        return DiffUtils(config=processing.validate())

    def create_pattern_matching_service(self) -> PatternMatchingServiceInterface:
        """Create pattern matching service instance."""
        settings_service = get_settings_service()
        github_settings = settings_service.get_github_settings()

        ignore_patterns = github_settings.get("ignore_patterns", [])
        valid_extensions = github_settings.get("valid_extensions", [])

        return PatternMatcher(
            ignore_patterns=list(ignore_patterns) if ignore_patterns else [],
            valid_extensions=list(valid_extensions) if valid_extensions else [],
        )

    def create_pr_diff_service(self) -> SessionPRDiffReader:
        """Create PR diff service wired with one authoritative GitHubConfig."""
        from prdiffer.infrastructure.github.client import GitHubAPIClient

        config = get_settings_service().get_github_config()
        github_api_service = self.create_github_api_service()
        diff_service = self.create_diff_service()
        pattern_matching_service = self.create_pattern_matching_service()
        logger_service = self.create_logger_service()

        max_workers = config.github_worker_capacity
        file_processor = FileProcessor(
            github_api_service=github_api_service,
            pattern_matcher=pattern_matching_service,
            diff_utils=diff_service,
            max_files_allowed=config.max_files_allowed,
            parallel_fetch_threshold=10 if config.parallel_file_fetch_enabled else 10**9,
            max_parallel_workers=max_workers,
            parallel_head_base_fetch_enabled=config.parallel_head_base_fetch_enabled,
            require_git_tree=True,
        )

        diff_generator = get_diff_generator(
            diff_utils=diff_service,
            parallel_executor=None,
            parallel_enabled=config.parallel_diff_generation_enabled,
            parallel_threshold=config.diff_parallel_threshold,
            max_workers=config.diff_max_workers,
        )

        return GitHubPRDiffService(
            github_api_client=github_api_service if isinstance(github_api_service, GitHubAPIClient) else None,
            diff_generator=diff_generator,
            file_processor=file_processor,
            logger=logger_service,
            max_total_chars=config.max_total_chars,
            github_timeout_seconds=config.timeout,
            pr_diff_request_timeout_seconds=config.pr_diff_request_timeout_seconds,
        )

    def create_file_processor(self) -> FileProcessor:
        """Create file processor instance from GitHubConfig."""
        config = get_settings_service().get_github_config()
        github_api_service = self.create_github_api_service()
        diff_service = self.create_diff_service()
        pattern_matching_service = self.create_pattern_matching_service()

        return FileProcessor(
            github_api_service=github_api_service,
            pattern_matcher=pattern_matching_service,
            diff_utils=diff_service,
            max_files_allowed=config.max_files_allowed,
            parallel_fetch_threshold=10 if config.parallel_file_fetch_enabled else 10**9,
            require_git_tree=True,
            max_parallel_workers=config.github_worker_capacity,
            parallel_head_base_fetch_enabled=config.parallel_head_base_fetch_enabled,
        )

    def create_diff_generator(self) -> DiffGenerator:
        """Create diff generator instance from GitHubConfig parallel flag."""
        config = get_settings_service().get_github_config()
        diff_service = self.create_diff_service()

        return get_diff_generator(
            diff_utils=diff_service,
            parallel_executor=None,
            parallel_enabled=config.parallel_diff_generation_enabled,
            parallel_threshold=config.diff_parallel_threshold,
            max_workers=config.diff_max_workers,
        )

    def create_input_validator(self) -> InputValidatorProtocol:
        """Create input validator instance."""
        from prdiffer.infrastructure.security.input_validator import InputValidator

        return InputValidator()

    def create_gitlab_runtime(self, private_token: str | None = None):
        """Create shared GitLab runtime (process-shared limiter)."""
        from prdiffer.infrastructure.vcs_providers.gitlab_runtime import GitLabRuntime

        config = get_settings_service().get_gitlab_config()
        if not hasattr(self, "_gitlab_runtime") or self._gitlab_runtime is None:
            self._gitlab_runtime = GitLabRuntime(config, private_token=private_token)
        return self._gitlab_runtime

    def create_gitlab_session_reader(self, private_token: str | None = None):
        """Assemble the session-capable GitLab strict full-diff reader."""
        from prdiffer.infrastructure.github.diff_generator import DiffGenerator
        from prdiffer.infrastructure.utils.diff_utils import DiffUtils
        from prdiffer.infrastructure.vcs_providers.gitlab_content import GitLabContentFetcher
        from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import GitLabDiffAssembler
        from prdiffer.infrastructure.vcs_providers.gitlab_diff_session import GitLabSessionPRDiffReader
        from prdiffer.infrastructure.vcs_providers.gitlab_operations import GitLabOperations
        from prdiffer.infrastructure.vcs_providers.gitlab_repository import GitLabVCSRepository

        config = get_settings_service().get_gitlab_config()
        runtime = self.create_gitlab_runtime(private_token=private_token)
        operations = GitLabOperations()
        content = GitLabContentFetcher(runtime, config, parallel_enabled=True)
        assembler = GitLabDiffAssembler(
            DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=config.max_concurrent > 1),
            config,
        )
        session_reader = GitLabSessionPRDiffReader(
            operations=operations,
            runtime=runtime,
            content_fetcher=content,
            assembler=assembler,
            config=config,
            request_timeout_seconds=config.pr_diff_request_timeout_seconds,
        )
        return GitLabVCSRepository(
            private_token,
            config=config,
            runtime=runtime,
            operations=operations,
            session_reader=session_reader,
        )


def get_infrastructure_factory() -> InfrastructureFactoryInterface:
    """Get infrastructure factory instance."""
    return InfrastructureFactory()
