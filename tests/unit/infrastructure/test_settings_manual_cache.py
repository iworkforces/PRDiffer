import threading
import time
from prdiffer.infrastructure.settings import SettingsService
from prdiffer.domain.config.github_config import GitHubConfig
from prdiffer.domain.config.gitlab_config import GitLabConfig


class TestSettingsCaching:
    def test_get_github_settings_caches_result(self):
        service = SettingsService()

        result1 = service.get_github_settings()
        result2 = service.get_github_settings()

        assert result1 is result2
        assert isinstance(result1, dict)

    def test_get_github_config_caches_result(self):
        service = SettingsService()

        result1 = service.get_github_config()
        result2 = service.get_github_config()

        assert result1 is result2
        assert isinstance(result1, GitHubConfig)

    def test_get_gitlab_config_caches_result(self):
        service = SettingsService()

        result1 = service.get_gitlab_config()
        result2 = service.get_gitlab_config()

        assert result1 is result2
        assert isinstance(result1, GitLabConfig)

    def test_get_cache_settings_caches_result(self):
        service = SettingsService()

        result1 = service.get_cache_settings()
        result2 = service.get_cache_settings()

        assert result1 is result2
        assert isinstance(result1, dict)

    def test_get_app_settings_caches_result(self):
        service = SettingsService()

        result1 = service.get_app_settings()
        result2 = service.get_app_settings()

        assert result1 is result2
        assert isinstance(result1, dict)


class TestCacheInvalidation:
    def test_clear_cache_invalidates_github_settings(self):
        service = SettingsService()

        result1 = service.get_github_settings()
        service.clear_cache()
        result2 = service.get_github_settings()

        assert result1 is not result2
        assert result1 == result2

    def test_clear_cache_invalidates_github_config(self):
        service = SettingsService()

        result1 = service.get_github_config()
        service.clear_cache()
        result2 = service.get_github_config()

        assert result1 is not result2

    def test_clear_cache_invalidates_gitlab_config(self):
        service = SettingsService()

        result1 = service.get_gitlab_config()
        service.clear_cache()
        result2 = service.get_gitlab_config()

        assert result1 is not result2

    def test_clear_cache_invalidates_cache_settings(self):
        service = SettingsService()

        result1 = service.get_cache_settings()
        service.clear_cache()
        result2 = service.get_cache_settings()

        assert result1 is not result2
        assert result1 == result2

    def test_clear_cache_invalidates_app_settings(self):
        service = SettingsService()

        result1 = service.get_app_settings()
        service.clear_cache()
        result2 = service.get_app_settings()

        assert result1 is not result2
        assert result1 == result2

    def test_clear_cache_invalidates_all_caches(self):
        service = SettingsService()

        github_settings_1 = service.get_github_settings()
        github_config_1 = service.get_github_config()
        gitlab_config_1 = service.get_gitlab_config()
        cache_settings_1 = service.get_cache_settings()
        app_settings_1 = service.get_app_settings()

        service.clear_cache()

        github_settings_2 = service.get_github_settings()
        github_config_2 = service.get_github_config()
        gitlab_config_2 = service.get_gitlab_config()
        cache_settings_2 = service.get_cache_settings()
        app_settings_2 = service.get_app_settings()

        assert github_settings_1 is not github_settings_2
        assert gitlab_config_1 is not gitlab_config_2
        assert github_config_1 is not github_config_2
        assert cache_settings_1 is not cache_settings_2
        assert app_settings_1 is not app_settings_2


class TestThreadSafety:
    def test_concurrent_get_github_settings_safe(self):
        service = SettingsService()
        results = []
        errors = []

        def worker():
            try:
                result = service.get_github_settings()
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        assert all(r is results[0] for r in results)

    def test_concurrent_get_github_config_safe(self):
        service = SettingsService()
        results = []
        errors = []

        def worker():
            try:
                result = service.get_github_config()
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        assert all(r is results[0] for r in results)

    def test_concurrent_clear_cache_safe(self):
        service = SettingsService()
        errors = []

        def reader():
            try:
                service.get_github_settings()
                service.get_github_config()
            except Exception as e:
                errors.append(e)

        def clearer():
            try:
                service.clear_cache()
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(5):
            threads.append(threading.Thread(target=reader))
            threads.append(threading.Thread(target=clearer))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_no_race_condition_in_cache_initialization(self):
        service = SettingsService()
        call_count = {"get_github_settings": 0}
        original_get = service.get

        def tracked_get(key, default=None):
            if key.startswith("github."):
                call_count["get_github_settings"] += 1
                time.sleep(0.001)
            return original_get(key, default)

        service.get = tracked_get

        results = []

        def worker():
            result = service.get_github_settings()
            results.append(result)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is results[0] for r in results)


class TestSettingsValues:
    def test_get_github_settings_returns_expected_keys(self):
        service = SettingsService()
        settings = service.get_github_settings()

        expected_keys = {
            "rate_limit",
            "timeout",
            "max_retries",
            "retry_delay",
            "retry_on_404",
            "retry_on_403",
            "retry_on_500",
            "retry_log_level",
            "permanent_failure_log_level",
            "circuit_breaker_enabled",
            "circuit_breaker_failure_threshold",
            "circuit_breaker_timeout",
            "adaptive_retry_enabled",
            "max_adaptive_delay",
            "api_health_tracking",
            "context_aware_retry",
            "ignore_patterns",
            "valid_extensions",
            "diff_parallel_threshold",
            "diff_max_workers",
            "max_concurrent",
        }

        assert set(settings.keys()) == expected_keys

    def test_get_github_settings_tuples_are_immutable(self):
        service = SettingsService()
        settings = service.get_github_settings()

        assert isinstance(settings["ignore_patterns"], tuple)
        assert isinstance(settings["valid_extensions"], tuple)

    def test_get_cache_settings_returns_expected_keys(self):
        service = SettingsService()
        settings = service.get_cache_settings()

        expected_keys = {"ttl", "max_size", "enabled"}
        assert set(settings.keys()) == expected_keys

    def test_get_app_settings_returns_expected_keys(self):
        service = SettingsService()
        settings = service.get_app_settings()

        expected_keys = {
            "debug",
            "log_level",
            "max_files_allowed",
            "incremental_mode",
            "logging_enabled",
            "log_format",
        }
        assert set(settings.keys()) == expected_keys

    def test_get_github_config_returns_dataclass(self):
        service = SettingsService()
        config = service.get_github_config()

        assert isinstance(config, GitHubConfig)
        assert hasattr(config, "rate_limit")
        assert hasattr(config, "timeout")
        assert hasattr(config, "max_retries")


class TestNoLruCacheImport:
    def test_no_lru_cache_decorator_in_code(self):
        from pathlib import Path

        settings_path = Path(__file__).resolve().parents[3] / "prdiffer" / "infrastructure" / "settings.py"
        lines = settings_path.read_text(encoding="utf-8").splitlines(keepends=True)

        for line in lines:
            stripped = line.lstrip()
            assert not stripped.startswith("@lru_cache"), f"Found @lru_cache decorator: {line.strip()}"
        source = "".join(lines)
        assert "from functools import lru_cache" not in source
