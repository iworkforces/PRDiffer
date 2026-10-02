"""Tests for concurrency settings in infrastructure layer.

These tests verify that concurrency settings are properly read
from settings.toml and applied to the GitHub session capacity limiter.
"""

from unittest.mock import MagicMock

from prdiffer.domain.config.github_config import GitHubConfig
from prdiffer.infrastructure.github.pr_diff_session import GitHubSessionPRDiffReader
from prdiffer.infrastructure.settings import SettingsService


class TestConcurrencySettings:
    """Test concurrency configuration settings."""

    def test_session_reader_respects_max_concurrent_setting(self):
        """Verify that the session reader limiter uses max_concurrent from settings."""
        settings_service = SettingsService()

        # Default value from settings.toml
        default_max_concurrent = settings_service.get("github.max_concurrent", 4)

        reader = GitHubSessionPRDiffReader(MagicMock(), max_concurrent=default_max_concurrent)

        assert reader._limiter.total_tokens == default_max_concurrent

    def test_session_reader_can_override_max_concurrent(self):
        """Verify that the session reader capacity can be overridden."""
        reader = GitHubSessionPRDiffReader(MagicMock(), max_concurrent=8)

        assert reader._limiter.total_tokens == 8

    def test_session_reader_serializes_when_parallel_fetch_disabled(self):
        """Verify that disabling parallel fetch serializes session capacity."""
        reader = GitHubSessionPRDiffReader(MagicMock(), parallel_file_fetch_enabled=False, max_concurrent=8)

        assert reader._limiter.total_tokens == 1
        assert GitHubConfig(parallel_file_fetch_enabled=False, max_concurrent=8).github_worker_capacity == 1

    def test_concurrency_settings_have_reasonable_defaults(self):
        """Verify that default concurrency settings are reasonable values."""
        settings_service = SettingsService()

        github_max_concurrent = settings_service.get("github.max_concurrent", 4)
        assert 1 <= github_max_concurrent <= 20, "GitHub max_concurrent should be between 1 and 20"

    def test_async_parallel_executor_configurable(self):
        """Verify that AsyncParallelExecutor can be configured with different concurrency values."""
        from prdiffer.infrastructure.utils.parallel.executor import (
            AsyncParallelExecutor,
        )
        from prdiffer.infrastructure.utils.parallel.results import (
            ErrorStrategy,
        )

        # Test with different concurrency values
        for max_concurrent in [1, 4, 8, 16]:
            executor = AsyncParallelExecutor(
                max_concurrent=max_concurrent,
                error_strategy=ErrorStrategy.IGNORE,
            )
            assert executor.max_concurrent == max_concurrent
