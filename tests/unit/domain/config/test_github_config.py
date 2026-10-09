"""Tests for GitHubConfig frozen dataclass."""

import pytest

from prdiffer.domain.config.github_config import GitHubConfig
from prdiffer.domain.config.github_config_interface import GitHubConfigInterface


@pytest.mark.unit
class TestGitHubConfigDefaults:
    """Test default values on GitHubConfig."""

    def test_default_rate_limit(self):
        config = GitHubConfig()
        assert config.rate_limit == 5000

    def test_default_timeout(self):
        config = GitHubConfig()
        assert config.timeout == 30

    def test_default_max_retries(self):
        config = GitHubConfig()
        assert config.max_retries == 3

    def test_default_retry_delay(self):
        config = GitHubConfig()
        assert config.retry_delay == 1.0

    def test_default_retry_flags(self):
        config = GitHubConfig()
        assert config.retry_on_404 is False
        assert config.retry_on_403 is True
        assert config.retry_on_500 is True

    def test_default_circuit_breaker(self):
        config = GitHubConfig()
        assert config.circuit_breaker_enabled is True
        assert config.circuit_breaker_failure_threshold == 5
        assert config.circuit_breaker_timeout == 60

    def test_default_ignore_patterns_empty(self):
        config = GitHubConfig()
        assert config.ignore_patterns == ()

    def test_default_valid_extensions_empty(self):
        config = GitHubConfig()
        assert config.valid_extensions == ()

    def test_default_parallel_settings(self):
        config = GitHubConfig()
        assert config.diff_parallel_threshold == 3
        assert config.diff_max_workers == 4

    def test_default_size_limits(self):
        config = GitHubConfig()
        assert config.max_files_allowed == 50
        assert config.large_file_threshold == 5000
        assert config.chunk_size == 1000
        assert config.max_diff_size == 100000
        assert config.max_file_size_bytes == 10_485_760
        assert config.pr_diff_request_timeout_seconds == 180.0
        assert config.parallel_file_fetch_enabled is True
        assert config.parallel_diff_generation_enabled is True


@pytest.mark.unit
class TestGitHubConfigFrozen:
    """Test immutability of frozen dataclass."""

    def test_cannot_set_attribute(self):
        config = GitHubConfig()
        with pytest.raises(AttributeError):
            setattr(config, "rate_limit", 999)

    def test_is_hashable(self):
        config = GitHubConfig()
        h = hash(config)
        assert isinstance(h, int)

    def test_two_equal_configs_have_same_hash(self):
        c1 = GitHubConfig()
        c2 = GitHubConfig()
        assert hash(c1) == hash(c2)

    def test_implements_protocol(self):
        config = GitHubConfig()
        assert isinstance(config, GitHubConfigInterface)


@pytest.mark.unit
class TestGitHubConfigFromDict:
    """Test from_dict class method."""

    def test_from_empty_dict_uses_defaults(self):
        config = GitHubConfig.from_dict({})
        assert config.rate_limit == 5000
        assert config.timeout == 30
        assert config.max_retries == 3

    def test_from_dict_overrides_values(self):
        config = GitHubConfig.from_dict(
            {
                "rate_limit": 1000,
                "timeout": 60,
                "max_retries": 5,
            }
        )
        assert config.rate_limit == 1000
        assert config.timeout == 60
        assert config.max_retries == 5

    def test_from_dict_converts_list_ignore_patterns_to_tuple(self):
        config = GitHubConfig.from_dict(
            {
                "ignore_patterns": ["*.lock", "node_modules/"],
            }
        )
        assert config.ignore_patterns == ("*.lock", "node_modules/")
        assert isinstance(config.ignore_patterns, tuple)

    def test_from_dict_converts_list_valid_extensions_to_tuple(self):
        config = GitHubConfig.from_dict(
            {
                "valid_extensions": [".py", ".js"],
            }
        )
        assert config.valid_extensions == (".py", ".js")
        assert isinstance(config.valid_extensions, tuple)

    def test_from_dict_accepts_tuple_patterns_directly(self):
        patterns = (".py", ".js")
        config = GitHubConfig.from_dict(
            {
                "valid_extensions": patterns,
            }
        )
        assert config.valid_extensions == patterns

    def test_from_dict_handles_none_patterns(self):
        config = GitHubConfig.from_dict(
            {
                "ignore_patterns": None,
                "valid_extensions": None,
            }
        )
        assert config.ignore_patterns == ()
        assert config.valid_extensions == ()

    def test_from_dict_retry_delay_as_int_becomes_float(self):
        config = GitHubConfig.from_dict({"retry_delay": 2})
        assert config.retry_delay == 2.0
        assert isinstance(config.retry_delay, float)

    def test_from_dict_all_fields(self):
        data = {
            "rate_limit": 100,
            "timeout": 10,
            "max_retries": 1,
            "retry_delay": 0.5,
            "retry_on_404": True,
            "retry_on_403": False,
            "retry_on_500": False,
            "retry_log_level": "WARNING",
            "permanent_failure_log_level": "ERROR",
            "circuit_breaker_enabled": False,
            "circuit_breaker_failure_threshold": 10,
            "circuit_breaker_timeout": 120,
            "adaptive_retry_enabled": False,
            "max_adaptive_delay": 60,
            "api_health_tracking": False,
            "context_aware_retry": False,
            "ignore_patterns": ["*.lock"],
            "valid_extensions": [".py"],
            "diff_parallel_threshold": 5,
            "diff_max_workers": 8,
            "max_files_allowed": 100,
            "large_file_threshold": 10000,
            "chunk_size": 500,
            "max_diff_size": 50000,
        }
        config = GitHubConfig.from_dict(data)
        assert config.rate_limit == 100
        assert config.retry_on_404 is True
        assert config.circuit_breaker_enabled is False
        assert config.ignore_patterns == ("*.lock",)
        assert config.valid_extensions == (".py",)
        assert config.diff_parallel_threshold == 5
        assert config.max_diff_size == 50000


@pytest.mark.unit
class TestGitHubConfigToDict:
    """Test to_dict method."""

    def test_to_dict_returns_dict(self):
        config = GitHubConfig()
        result = config.to_dict()
        assert isinstance(result, dict)

    def test_to_dict_contains_all_fields(self):
        config = GitHubConfig()
        result = config.to_dict()
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
            "max_files_allowed",
            "large_file_threshold",
            "chunk_size",
            "max_diff_size",
            "max_file_size_bytes",
            "parallel_file_fetch_enabled",
            "parallel_diff_generation_enabled",
            "pr_diff_request_timeout_seconds",
            "max_concurrent",
        }
        assert set(result.keys()) == expected_keys

    def test_to_dict_converts_tuples_to_lists(self):
        config = GitHubConfig(
            ignore_patterns=("*.lock",),
            valid_extensions=(".py",),
        )
        result = config.to_dict()
        assert isinstance(result["ignore_patterns"], list)
        assert isinstance(result["valid_extensions"], list)
        assert result["ignore_patterns"] == ["*.lock"]
        assert result["valid_extensions"] == [".py"]

    def test_roundtrip_from_dict_to_dict(self):
        original = GitHubConfig(
            rate_limit=999,
            timeout=15,
            ignore_patterns=("*.md", "*.txt"),
            valid_extensions=(".py",),
        )
        roundtripped = GitHubConfig.from_dict(original.to_dict())
        assert roundtripped == original


@pytest.mark.unit
class TestGitHubConfigWithOverrides:
    """Test with_overrides method."""

    def test_override_single_field(self):
        config = GitHubConfig()
        new_config = config.with_overrides(rate_limit=999)
        assert new_config.rate_limit == 999
        assert new_config.timeout == config.timeout  # unchanged

    def test_override_multiple_fields(self):
        config = GitHubConfig()
        new_config = config.with_overrides(rate_limit=100, timeout=5)
        assert new_config.rate_limit == 100
        assert new_config.timeout == 5

    def test_override_does_not_mutate_original(self):
        config = GitHubConfig()
        _ = config.with_overrides(rate_limit=999)
        assert config.rate_limit == 5000

    def test_override_list_converted_to_tuple(self):
        config = GitHubConfig()
        new_config = config.with_overrides(ignore_patterns=["*.lock", "*.min.js"])
        assert new_config.ignore_patterns == ("*.lock", "*.min.js")
        assert isinstance(new_config.ignore_patterns, tuple)

    def test_override_returns_new_instance(self):
        config = GitHubConfig()
        new_config = config.with_overrides(timeout=99)
        assert config is not new_config


@pytest.mark.unit
class TestGitHubConfigFileFiltering:
    """Test should_ignore_file, has_valid_extension, should_process_file."""

    def test_should_ignore_file_matching_glob(self):
        config = GitHubConfig(ignore_patterns=("*.lock", "*.min.js"))
        assert config.should_ignore_file("package-lock.lock") is True
        assert config.should_ignore_file("app.min.js") is True

    def test_should_not_ignore_file_not_matching(self):
        config = GitHubConfig(ignore_patterns=("*.lock",))
        assert config.should_ignore_file("main.py") is False

    def test_should_ignore_file_case_insensitive(self):
        config = GitHubConfig(ignore_patterns=("*.Lock",))
        assert config.should_ignore_file("package.lock") is True
        assert config.should_ignore_file("package.LOCK") is True

    def test_should_ignore_file_directory_pattern(self):
        config = GitHubConfig(ignore_patterns=("node_modules/",))
        assert config.should_ignore_file("node_modules/lodash/index.js") is True

    def test_should_ignore_file_empty_patterns(self):
        config = GitHubConfig(ignore_patterns=())
        assert config.should_ignore_file("anything.py") is False

    def test_has_valid_extension_match(self):
        config = GitHubConfig(valid_extensions=(".py", ".js"))
        assert config.has_valid_extension("main.py") is True
        assert config.has_valid_extension("app.js") is True

    def test_has_valid_extension_no_match(self):
        config = GitHubConfig(valid_extensions=(".py",))
        assert config.has_valid_extension("style.css") is False

    def test_has_valid_extension_case_insensitive(self):
        config = GitHubConfig(valid_extensions=(".py",))
        assert config.has_valid_extension("main.PY") is True

    def test_has_valid_extension_no_restrictions(self):
        config = GitHubConfig(valid_extensions=())
        assert config.has_valid_extension("anything.xyz") is True

    def test_should_process_file_passes_both_checks(self):
        config = GitHubConfig(
            ignore_patterns=("*.lock",),
            valid_extensions=(".py", ".js"),
        )
        assert config.should_process_file("main.py") is True
        assert config.should_process_file("package.lock") is False
        assert config.should_process_file("style.css") is False

    def test_should_process_file_no_filters(self):
        config = GitHubConfig()
        assert config.should_process_file("anything.txt") is True


@pytest.mark.unit
class TestGitHubConfigProperties:
    """Test boolean property shortcuts."""

    def test_should_use_circuit_breaker(self):
        assert GitHubConfig(circuit_breaker_enabled=True).should_use_circuit_breaker is True
        assert GitHubConfig(circuit_breaker_enabled=False).should_use_circuit_breaker is False

    def test_should_use_adaptive_retry(self):
        assert GitHubConfig(adaptive_retry_enabled=True).should_use_adaptive_retry is True
        assert GitHubConfig(adaptive_retry_enabled=False).should_use_adaptive_retry is False

    def test_should_track_api_health(self):
        assert GitHubConfig(api_health_tracking=True).should_track_api_health is True
        assert GitHubConfig(api_health_tracking=False).should_track_api_health is False
