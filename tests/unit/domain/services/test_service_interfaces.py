import pytest
from abc import ABC

from prdiffer.domain.services.cache import CacheServiceInterface
from prdiffer.domain.services.diff import DiffServiceInterface
from prdiffer.domain.services.logger import LoggerServiceInterface, LogLevel
from prdiffer.domain.services.pattern_matching import PatternMatchingServiceInterface
from prdiffer.domain.services.pr_diff_service import PRDiffServiceInterface
from prdiffer.domain.services.repository_cache import RepositoryCacheServiceInterface
from prdiffer.domain.services.retry import RetryServiceInterface
from prdiffer.domain.services.settings import SettingsServiceInterface
from prdiffer.domain.services.github_api import GitHubAPIServiceInterface


class TestCacheServiceInterface:
    def test_is_abstract_base_class(self):
        assert issubclass(CacheServiceInterface, ABC)
        assert hasattr(CacheServiceInterface, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            CacheServiceInterface()

    def test_has_required_abstract_methods(self):
        abstract_methods = CacheServiceInterface.__abstractmethods__

        required_methods = {
            "get_cache_key",
            "get",
            "set",
            "invalidate",
            "get_etag",
            "set_etag",
            "get_stats",
        }

        assert required_methods.issubset(abstract_methods)


class TestLoggerServiceInterface:
    def test_is_abstract_base_class(self):
        assert issubclass(LoggerServiceInterface, ABC)
        assert hasattr(LoggerServiceInterface, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            LoggerServiceInterface()

    def test_has_required_abstract_methods(self):
        abstract_methods = LoggerServiceInterface.__abstractmethods__

        required_methods = {
            "debug",
            "info",
            "warning",
            "error",
            "critical",
        }

        assert required_methods.issubset(abstract_methods)


class TestLogLevelEnum:
    def test_log_level_exists(self):
        assert LogLevel is not None

    def test_log_level_values(self):
        assert hasattr(LogLevel, "__members__")
        level_names = [name for name, _ in LogLevel.__members__.items()]
        assert len(level_names) >= 5


class TestSettingsServiceInterface:
    def test_is_abstract_base_class(self):
        assert issubclass(SettingsServiceInterface, ABC)
        assert hasattr(SettingsServiceInterface, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            SettingsServiceInterface()

    def test_has_required_abstract_methods(self):
        abstract_methods = SettingsServiceInterface.__abstractmethods__

        required_methods = {
            "get",
            "get_github_settings",
            "get_app_settings",
        }

        assert required_methods.issubset(abstract_methods)


class TestRetryServiceInterface:
    def test_is_abstract_base_class(self):
        assert issubclass(RetryServiceInterface, ABC)
        assert hasattr(RetryServiceInterface, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            RetryServiceInterface()

    def test_has_required_abstract_methods(self):
        abstract_methods = RetryServiceInterface.__abstractmethods__

        required_methods = {"execute_with_retry"}

        assert required_methods.issubset(abstract_methods)


class TestPatternMatchingServiceInterface:
    def test_is_abstract_base_class(self):
        assert issubclass(PatternMatchingServiceInterface, ABC)
        assert hasattr(PatternMatchingServiceInterface, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            PatternMatchingServiceInterface()

    def test_has_required_abstract_methods(self):
        abstract_methods = PatternMatchingServiceInterface.__abstractmethods__

        required_methods = {"is_valid_file", "filter_files"}

        assert required_methods.issubset(abstract_methods)


class TestDiffServiceInterface:
    def test_is_abstract_base_class(self):
        assert issubclass(DiffServiceInterface, ABC)
        assert hasattr(DiffServiceInterface, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            DiffServiceInterface()

    def test_has_required_abstract_methods(self):
        abstract_methods = DiffServiceInterface.__abstractmethods__

        required_methods = {
            "build_full_file_patch",
            "decode_if_bytes",
            "extend_patch",
        }

        assert required_methods.issubset(abstract_methods)


class TestRepositoryCacheServiceInterface:
    def test_is_abstract_base_class(self):
        assert issubclass(RepositoryCacheServiceInterface, ABC)
        assert hasattr(RepositoryCacheServiceInterface, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            RepositoryCacheServiceInterface()

    def test_has_required_abstract_methods(self):
        abstract_methods = RepositoryCacheServiceInterface.__abstractmethods__

        required_methods = {
            "insert",
            "retrieve",
            "validate",
            "remove",
            "clear",
            "size",
            "stats",
        }

        assert required_methods.issubset(abstract_methods)


class TestGitHubAPIServiceInterface:
    def test_is_abstract_base_class(self):
        assert issubclass(GitHubAPIServiceInterface, ABC)
        assert hasattr(GitHubAPIServiceInterface, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            GitHubAPIServiceInterface()

    def test_has_required_abstract_methods(self):
        abstract_methods = GitHubAPIServiceInterface.__abstractmethods__
        assert len(abstract_methods) > 0


class TestPRDiffServiceInterface:
    def test_is_abstract_base_class(self):
        assert issubclass(PRDiffServiceInterface, ABC)
        assert hasattr(PRDiffServiceInterface, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            PRDiffServiceInterface()

    def test_has_required_abstract_methods(self):
        abstract_methods = PRDiffServiceInterface.__abstractmethods__

        required_methods = {
            "get_pr_diff",
            "get_latest_commit_sha",
            "validate_repository_access",
        }

        assert required_methods.issubset(abstract_methods)


class TestInterfaceStructure:
    def test_all_interfaces_are_abstract(self):
        interfaces = [
            CacheServiceInterface,
            DiffServiceInterface,
            LoggerServiceInterface,
            PatternMatchingServiceInterface,
            PRDiffServiceInterface,
            RepositoryCacheServiceInterface,
            RetryServiceInterface,
            SettingsServiceInterface,
            GitHubAPIServiceInterface,
        ]

        for interface in interfaces:
            assert issubclass(interface, ABC), f"{interface.__name__} should inherit from ABC"

    def test_all_interfaces_have_abstract_methods(self):
        interfaces = [
            CacheServiceInterface,
            DiffServiceInterface,
            LoggerServiceInterface,
            PatternMatchingServiceInterface,
            PRDiffServiceInterface,
            RepositoryCacheServiceInterface,
            RetryServiceInterface,
            SettingsServiceInterface,
            GitHubAPIServiceInterface,
        ]

        for interface in interfaces:
            assert len(interface.__abstractmethods__) > 0, f"{interface.__name__} should have abstract methods"

    def test_all_interfaces_cannot_be_instantiated(self):
        interfaces = [
            CacheServiceInterface,
            DiffServiceInterface,
            LoggerServiceInterface,
            PatternMatchingServiceInterface,
            PRDiffServiceInterface,
            RepositoryCacheServiceInterface,
            RetryServiceInterface,
            SettingsServiceInterface,
            GitHubAPIServiceInterface,
        ]

        for interface in interfaces:
            with pytest.raises(TypeError, match=r"(abstract|instantiated)"):
                interface()

    def test_interface_names_follow_convention(self):
        interfaces = [
            CacheServiceInterface,
            DiffServiceInterface,
            LoggerServiceInterface,
            PatternMatchingServiceInterface,
            PRDiffServiceInterface,
            RepositoryCacheServiceInterface,
            RetryServiceInterface,
            SettingsServiceInterface,
            GitHubAPIServiceInterface,
        ]

        for interface in interfaces:
            assert interface.__name__.endswith("Interface"), f"{interface.__name__} should end with 'Interface' suffix"

    def test_interfaces_have_docstrings(self):
        interfaces = [
            CacheServiceInterface,
            DiffServiceInterface,
            LoggerServiceInterface,
            PatternMatchingServiceInterface,
            PRDiffServiceInterface,
            RepositoryCacheServiceInterface,
            RetryServiceInterface,
            SettingsServiceInterface,
            GitHubAPIServiceInterface,
        ]

        for interface in interfaces:
            assert interface.__doc__ is not None, f"{interface.__name__} should have a docstring"
            assert len(interface.__doc__.strip()) > 0, f"{interface.__name__} docstring should not be empty"


class TestMockImplementationCompliance:
    def test_can_create_mock_cache_service(self):
        class MockCacheService(CacheServiceInterface):
            def __init__(self):
                self._data = {}

            def get_cache_key(self, repo_owner: str, repo_name: str, pr_number: int) -> str:
                return f"{repo_owner}/{repo_name}/pr/{pr_number}"

            def get(self, cache_key: str, current_commit_sha: str):
                return self._data.get(cache_key)

            def set(self, cache_key: str, commit_sha: str, data):
                self._data[cache_key] = data

            def invalidate(self, cache_key: str):
                self._data.pop(cache_key, None)

            async def invalidate_github_pr(self, owner: str, repo: str, pr_number: int) -> None:
                self.invalidate(self.get_cache_key(owner, repo, pr_number))

            async def invalidate_github_repository(self, owner: str, repo: str) -> None:
                prefix = f"{owner}/{repo}/pr/"
                for key in tuple(self._data):
                    if key.startswith(prefix):
                        self.invalidate(key)

            def clear(self):
                self._data.clear()

            def get_etag(self, cache_key: str):
                return None

            def set_etag(self, cache_key: str, etag: str):
                pass

            def get_stats(self):
                return {"size": len(self._data)}

            async def get_optimistic(self, cache_key: str):
                """Optimistic cache lookup without commit SHA."""
                return None, None

        mock = MockCacheService()
        assert isinstance(mock, CacheServiceInterface)

    def test_can_create_mock_logger_service(self):
        class MockLoggerService(LoggerServiceInterface):
            def __init__(self):
                self.messages = []

            def _log(self, level, message, **kwargs):
                self.messages.append({"level": level, "message": message, **kwargs})

            def debug(self, message: str, **kwargs):
                self._log("DEBUG", message, **kwargs)

            def info(self, message: str, **kwargs):
                self._log("INFO", message, **kwargs)

            def warning(self, message: str, **kwargs):
                self._log("WARNING", message, **kwargs)

            def error(self, message: str, **kwargs):
                self._log("ERROR", message, **kwargs)

            def critical(self, message: str, **kwargs):
                self._log("CRITICAL", message, **kwargs)

            def should_log(self, level):
                return True  # Log everything

        mock = MockLoggerService()
        assert isinstance(mock, LoggerServiceInterface)

    def test_can_create_mock_settings_service(self):
        class MockSettingsService(SettingsServiceInterface):
            def __init__(self):
                self._settings = {}

            def get(self, key, default=None):
                return self._settings.get(key, default)

            def get_github_settings(self):
                return {
                    "rate_limit": 5000,
                    "timeout": 30,
                }

            def get_cache_settings(self):
                return {
                    "default_ttl": 300,
                    "max_size": 1000,
                }

            def get_app_settings(self):
                return {
                    "debug": False,
                    "log_level": "INFO",
                }

            def clear_cache(self):
                self._settings.clear()

        mock = MockSettingsService()
        assert isinstance(mock, SettingsServiceInterface)

    def test_can_create_mock_retry_service(self):
        class MockRetryService(RetryServiceInterface):
            def execute_with_retry(self, func, *args, **kwargs):
                return func(*args, **kwargs)

            def _is_rate_limit_error(self, error):
                return False

        mock = MockRetryService()
        assert isinstance(mock, RetryServiceInterface)

    def test_can_create_mock_pattern_matching_service(self):
        class MockPatternMatchingService(PatternMatchingServiceInterface):
            def __init__(self, patterns=None, extensions=None):
                self.patterns = patterns or []
                self.extensions = extensions or []

            def is_valid_file(self, filename):
                return not any(p in filename for p in self.patterns)

            def filter_files(self, filenames):
                return [f for f in filenames if self.is_valid_file(f)]

        mock = MockPatternMatchingService()
        assert isinstance(mock, PatternMatchingServiceInterface)

    def test_can_create_mock_diff_service(self):
        class MockDiffService(DiffServiceInterface):
            def build_full_file_patch(self, original_file_str, new_file_str):
                return f"@@ -1,1 +1,1 @@\n-{original_file_str}\n+{new_file_str}\n"

            def decode_if_bytes(self, content):
                if isinstance(content, bytes):
                    return content.decode("utf-8")
                return str(content)

            def extend_patch(self, original_file_str, patch_str, new_file_str=""):
                return patch_str

        mock = MockDiffService()
        assert isinstance(mock, DiffServiceInterface)

    def test_can_create_mock_repository_cache_service(self):
        class MockRepositoryCacheService(RepositoryCacheServiceInterface):
            def __init__(self):
                self._cache = {}

            def insert(self, repository):
                key = f"{repository.repo_owner}/{repository.repo_name}/{repository.pr_number}"
                self._cache[key] = repository
                return True

            def retrieve(self, owner, name, pr_number):
                key = f"{owner}/{name}/{pr_number}"
                return self._cache.get(key)

            def validate(self, owner, name, pr_number):
                return f"{owner}/{name}/{pr_number}" in self._cache

            def remove(self, owner, name, pr_number):
                key = f"{owner}/{name}/{pr_number}"
                return self._cache.pop(key, None) is not None

            def clear(self):
                self._cache.clear()

            def size(self):
                return len(self._cache)

            def stats(self):
                return {"size": len(self._cache), "keys": list(self._cache.keys())}

            def invalidate(self, cache_key: str) -> bool:
                key = cache_key
                return self._cache.pop(key, None) is not None

            def invalidate_github_pr(self, owner: str, repo: str, pr_number: int) -> None:
                self.remove(owner, repo, pr_number)

            def invalidate_github_repository(self, owner: str, repo: str) -> None:
                prefix = f"{owner}/{repo}/"
                for key in tuple(self._cache):
                    if key.startswith(prefix):
                        self.invalidate(key)

        mock = MockRepositoryCacheService()
        assert isinstance(mock, RepositoryCacheServiceInterface)

    def test_can_create_mock_pr_diff_service(self):
        class MockPRDiffService(PRDiffServiceInterface):
            def get_pr_diff(self, repo_owner, repo_name, pr_number):
                return None

            def get_latest_commit_sha(self, repo_owner, repo_name, pr_number):
                return "abc123"

            def validate_repository_access(self, repo_owner, repo_name):
                return True

        mock = MockPRDiffService()
        assert isinstance(mock, PRDiffServiceInterface)

    def test_can_create_mock_github_api_service(self):
        class MockGitHubAPIService(GitHubAPIServiceInterface):
            def __init__(self):
                self.initialized = False

            def initialize_client(self, github_token=None, timeout=30):
                self.initialized = True

            def get_repository(self, repo_full_name):
                return None

            def get_pull_request(self, repository, pr_number):
                return None

            def get_file_content(self, repository, file_path, branch):
                return ""

            def get_files_content_batch(self, repository, file_paths, branch):
                return {}

            def get_files_content_multi_ref_batch(self, requests):
                return ()

        mock = MockGitHubAPIService()
        assert isinstance(mock, GitHubAPIServiceInterface)
