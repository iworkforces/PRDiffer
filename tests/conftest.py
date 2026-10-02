"""Pytest configuration and shared fixtures for PRDiffer tests.

This module provides common fixtures and configuration used across all test suites,
including mocks for external dependencies (GitHub API, cache, settings, etc.).
"""

import os
from typing import Any, Optional
from unittest.mock import Mock, AsyncMock
from unittest.mock import patch
import pytest

# Domain imports
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.file_patch import FilePatchInfo, EDIT_TYPE


# =============================================================================
# Test Configuration
# =============================================================================


def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    config.addinivalue_line("markers", "unit: Unit tests (isolated, fast, no external dependencies)")
    config.addinivalue_line("markers", "integration: Integration tests (may use external services)")
    config.addinivalue_line("markers", "slow: Slow-running tests")
    config.addinivalue_line("markers", "security: Security and vulnerability tests")
    config.addinivalue_line("markers", "thread_safety: Thread safety and concurrency tests")


# =============================================================================
# Mock Settings Fixtures
# =============================================================================


@pytest.fixture
def mock_settings():
    """Mock settings service with realistic default values."""
    mock = Mock()
    mock.get.side_effect = lambda key, default=None: {
        "app": {
            "debug": False,
            "log_level": "INFO",
            "max_files_allowed": 50,
        },
        "github": {
            "rate_limit": 5000,
            "timeout": 30,
            "max_retries": 3,
            "retry_delay": 1,
            "ignore_patterns": ["*.lock", "node_modules/", "dist/", "build/"],
            "valid_extensions": [".py", ".js", ".ts", ".md", ".yml", ".yaml"],
            "retry_on_404": False,
            "retry_on_403": True,
            "retry_on_500": True,
            "retry_log_level": "DEBUG",
            "permanent_failure_log_level": "INFO",
            "circuit_breaker_enabled": True,
            "circuit_breaker_failure_threshold": 5,
            "circuit_breaker_timeout": 60.0,
            "adaptive_retry_enabled": True,
            "max_adaptive_delay": 30.0,
            "api_health_tracking": True,
            "context_aware_retry": True,
            "use_advanced_retry": True,
            "diff_parallel_enabled": True,
            "diff_parallel_threshold": 3,
            "diff_max_workers": 4,
            "diff_worker_timeout": 30.0,
        },
        "cache": {
            "use_hashed_keys": True,
            "hash_algorithm": "md5",
            "store_key_mapping": True,
            "default_ttl": 300,
        },
        "mcp": {
            "transport": "stdio",
            "port": 9102,
            "host": "127.0.0.1",
        },
    }.get(key, default)

    def get_github_settings(self) -> dict[str, Any]:
        return {
            "rate_limit": 5000,
            "timeout": 30,
            "max_retries": 3,
            "retry_delay": 1,
            "ignore_patterns": ("*.lock", "node_modules/", "dist/", "build/"),
            "valid_extensions": (".py", ".js", ".ts", ".md", ".yml", ".yaml"),
            "retry_on_404": False,
            "retry_on_403": True,
            "retry_on_500": True,
            "retry_log_level": "DEBUG",
            "permanent_failure_log_level": "INFO",
            "circuit_breaker_enabled": True,
            "circuit_breaker_failure_threshold": 5,
            "circuit_breaker_timeout": 60.0,
            "adaptive_retry_enabled": True,
            "max_adaptive_delay": 30.0,
            "api_health_tracking": True,
            "context_aware_retry": True,
            "use_advanced_retry": True,
            "diff_parallel_enabled": True,
            "diff_parallel_threshold": 3,
            "diff_max_workers": 4,
            "diff_worker_timeout": 30.0,
        }

    def get_app_settings(self) -> dict[str, Any]:
        return {
            "debug": False,
            "log_level": "INFO",
            "max_files_allowed": 50,
        }

    mock.get_github_settings = get_github_settings
    mock.get_app_settings = get_app_settings
    return mock


@pytest.fixture
def mock_logger():
    """Mock logger service that captures log records."""
    mock = Mock()
    mock.records = []

    def make_log_method(level: str):
        def log_method(message: str, **kwargs):
            mock.records.append(
                {
                    "level": level,
                    "message": message,
                    "context": kwargs,
                }
            )

        return log_method

    mock.debug = make_log_method("DEBUG")
    mock.info = make_log_method("INFO")
    mock.warning = make_log_method("WARNING")
    mock.error = make_log_method("ERROR")
    mock.critical = make_log_method("CRITICAL")

    mock.level = 20  # INFO level
    return mock


@pytest.fixture
def mock_cache():
    """Mock cache service with in-memory storage."""
    mock = Mock()
    mock._data = {}
    mock._commit_shas = {}

    def get(key: str, commit_sha: Optional[str] = None) -> Optional[Any]:
        if commit_sha and key in mock._commit_shas:
            cached_sha = mock._commit_shas.get(key)
            if cached_sha != commit_sha:
                return None
        return mock._data.get(key)

    def set(key: str, value: Any, commit_sha: Optional[str] = None) -> None:
        mock._data[key] = value
        if commit_sha:
            mock._commit_shas[key] = commit_sha

    def invalidate(key: str) -> None:
        mock._data.pop(key, None)
        mock._commit_shas.pop(key, None)

    def clear(self) -> None:
        mock._data.clear()
        mock._commit_shas.clear()

    def size(self) -> int:
        return len(mock._data)

    mock.get = get
    mock.set = set
    mock.invalidate = invalidate
    mock.clear = clear
    mock.size = size
    return mock


# =============================================================================
# GitHub API Mocks
# =============================================================================


@pytest.fixture
def mock_github_repository():
    """Mock GitHub Repository object."""
    mock = Mock()
    mock.full_name = "test-owner/test-repo"
    mock.owner.login = "test-owner"
    mock.name = "test-repo"
    mock.default_branch = "main"
    return mock


@pytest.fixture
def mock_github_pull_request():
    """Mock GitHub PullRequest object."""
    mock = Mock()
    mock.number = 123
    mock.state = "open"
    mock.title = "Test PR"
    mock.body = "Test PR body"

    # Mock base and head refs
    mock.base.ref = "main"
    mock.base.sha = "abc123def456"
    mock.base.repo.full_name = "test-owner/test-repo"

    mock.head.ref = "feature-branch"
    mock.head.sha = "def456abc123"
    mock.head.repo.full_name = "test-owner/test-repo"

    # Mock merge base
    mock.merge_base_commit = Mock()
    mock.merge_base_commit.sha = "abc123base"

    return mock


@pytest.fixture
def mock_github_file():
    """Mock GitHub File object for PR files."""

    def make_file(filename: str, status: str = "modified", additions: int = 10, deletions: int = 5):
        mock = Mock()
        mock.filename = filename
        mock.status = status
        mock.additions = additions
        mock.deletions = deletions
        mock.changes = additions + deletions
        mock.patch = "@@ -1,5 +1,10 @@\n+new content\n-old content\n remaining"
        return mock

    return make_file


# =============================================================================
# Domain Entity Fixtures
# =============================================================================


@pytest.fixture
def sample_pr_diff():
    """Create a sample PRDiff entity for testing."""
    return PRDiff(files=())


@pytest.fixture
def sample_file_patch_info():
    """Create a sample FilePatchInfo entity for testing."""
    return FilePatchInfo(
        filename="src/test.py",
        patch="@@ -1,3 +1,5 @@\n old\n+new\n",
        base_file="line1\nline2\nline3\n",
        head_file="line1\nline2\nnew\n",
        edit_type=EDIT_TYPE.MODIFIED,
        num_plus_lines=1,
        num_minus_lines=1,
        language="Python",
    )


@pytest.fixture
def sample_file_patch_info_list():
    """Create a list of sample FilePatchInfo entities."""
    return [
        FilePatchInfo(
            filename=f"src/file{i}.py",
            patch="@@ -1,3 +1,5 @@\n",
            base_file=f"old content {i}",
            head_file=f"new content {i}",
            edit_type=EDIT_TYPE.MODIFIED if i > 0 else EDIT_TYPE.ADDED,
            num_plus_lines=5,
            num_minus_lines=3,
            language="Python",
        )
        for i in range(3)
    ]


# =============================================================================
# Async Test Utilities
# =============================================================================


@pytest.fixture
def async_mock():
    """Create an async mock object."""
    return AsyncMock()


# =============================================================================
# Environment Setup
# =============================================================================


@pytest.fixture(autouse=True)
def set_test_environment():
    """Set test environment variables for all tests."""
    original_env = os.environ.copy()

    # Set test environment variables
    os.environ["ENV_FOR_DYNACONF"] = "testing"
    os.environ["GITHUB_TOKEN"] = "test_token_ghp_test123456789"

    yield

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singleton instances between tests."""
    # Reset cache services
    import prdiffer.infrastructure.cache.service as cache_module

    cache_module._cache_service = None

    import prdiffer.infrastructure.settings as settings_module

    settings_module._settings_service = None

    import prdiffer.infrastructure.logging.console_logger as logger_module

    logger_module._logger_instance = None

    yield

    # Clean up after test
    cache_module._cache_service = None
    settings_module._settings_service = None
    logger_module._logger_instance = None


# =============================================================================
# Test Data Generators
# =============================================================================


@pytest.fixture
def generate_pr_url():
    """Generate valid GitHub PR URLs for testing."""

    def _generate(owner: str = "test-owner", repo: str = "test-repo", pr_number: int = 123) -> str:
        return f"https://github.com/{owner}/{repo}/pull/{pr_number}"

    return _generate


@pytest.fixture
def generate_diff_content():
    """Generate sample diff content for testing."""

    def _generate(filename: str = "test.py") -> str:
        return f"""
## File: '{filename}'

@@ -1,5 +1,10 @@
 context line 1
-context line 2
+new line 2
 context line 3
 context line 4
+context line 5
 context line 6
"""

    return _generate


# =============================================================================
# Patch Context Managers
# =============================================================================


@pytest.fixture
def patch_settings():
    """Context manager to patch settings service."""

    def _patcher():
        return patch("prdiffer.infrastructure.settings.get_settings_service", autospec=True)

    return _patcher


@pytest.fixture
def patch_cache():
    """Context manager to patch cache service."""

    def _patcher():
        return patch("prdiffer.infrastructure.cache.get_cache_service", autospec=True)

    return _patcher


# =============================================================================
# Concurrency Test Utilities
# =============================================================================


@pytest.fixture
def run_concurrently():
    """Utility to run functions concurrently for thread safety testing."""

    async def _run_concurrently(funcs, max_concurrent=10):
        """Run async functions concurrently with limited concurrency."""
        import anyio

        async def run_one(func):
            return await func()

        async with anyio.create_task_group() as tg:
            semaphore = anyio.Semaphore(max_concurrent)

            async def limited_run(func):
                async with semaphore:
                    return await run_one(func)

            for func in funcs:
                tg.start_soon(limited_run, func)

    return _run_concurrently


# =============================================================================
# Coverage Configuration
# =============================================================================


@pytest.fixture
def coverage_config():
    """Return coverage configuration for tests."""
    return {
        "source": ["prdiffer"],
        "omit": [
            "*/tests/*",
            "*/test_*.py",
            "*/__pycache__/*",
            "*/conftest.py",
        ],
        "precision": 2,
        "show_missing": True,
    }
