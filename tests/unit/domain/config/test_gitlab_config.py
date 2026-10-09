"""Tests for GitLabConfig frozen dataclass validation and defaults."""

from __future__ import annotations

import pytest

from prdiffer.domain.config.gitlab_config import (
    DEFAULT_GITLAB_TIMEOUT_SECONDS,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_MAX_FILES_ALLOWED,
    DEFAULT_MAX_RETRIES,
    DEFAULT_PR_DIFF_REQUEST_TIMEOUT_SECONDS,
    GitLabConfig,
)


@pytest.mark.unit
class TestGitLabConfigDefaults:
    def test_defaults_match_plan_contract(self) -> None:
        config = GitLabConfig()
        assert config.timeout == DEFAULT_GITLAB_TIMEOUT_SECONDS == 30
        assert config.max_retries == DEFAULT_MAX_RETRIES == 3
        assert config.max_concurrent == DEFAULT_MAX_CONCURRENT == 4
        assert config.retry_transient_errors is True
        assert config.obey_rate_limit is True
        assert config.max_file_size_bytes == DEFAULT_MAX_FILE_SIZE_BYTES == 10_485_760
        assert config.max_files_allowed == DEFAULT_MAX_FILES_ALLOWED == 50
        assert config.pr_diff_request_timeout_seconds == DEFAULT_PR_DIFF_REQUEST_TIMEOUT_SECONDS == 180.0


@pytest.mark.unit
class TestGitLabConfigFrozen:
    def test_cannot_set_attribute(self) -> None:
        config = GitLabConfig()
        with pytest.raises(AttributeError):
            setattr(config, "timeout", 99)

    def test_is_hashable(self) -> None:
        config = GitLabConfig()
        assert isinstance(hash(config), int)


@pytest.mark.unit
class TestGitLabConfigValidation:
    @pytest.mark.parametrize(
        "kwargs,match",
        [
            ({"timeout": 0}, "timeout"),
            ({"timeout": -1}, "timeout"),
            ({"timeout": 180, "pr_diff_request_timeout_seconds": 180}, "strictly less"),
            ({"timeout": 200, "pr_diff_request_timeout_seconds": 180}, "strictly less"),
            ({"max_retries": -1}, "max_retries"),
            ({"max_concurrent": 0}, "max_concurrent"),
            ({"max_concurrent": -2}, "max_concurrent"),
            ({"max_file_size_bytes": 0}, "max_file_size_bytes"),
            ({"max_files_allowed": 0}, "max_files_allowed"),
            ({"pr_diff_request_timeout_seconds": 0}, "pr_diff_request_timeout_seconds"),
            ({"pr_diff_request_timeout_seconds": -1.0}, "pr_diff_request_timeout_seconds"),
        ],
    )
    def test_invalid_boundaries_raise_value_error(self, kwargs: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            GitLabConfig(**kwargs)

    def test_max_retries_zero_allowed(self) -> None:
        config = GitLabConfig(max_retries=0)
        assert config.max_retries == 0


@pytest.mark.unit
class TestGitLabConfigFromDict:
    def test_from_empty_dict_uses_defaults(self) -> None:
        config = GitLabConfig.from_dict({})
        assert config.timeout == 30
        assert config.max_retries == 3
        assert config.obey_rate_limit is True

    def test_from_dict_overrides(self) -> None:
        config = GitLabConfig.from_dict(
            {
                "timeout": 15,
                "max_retries": 1,
                "max_concurrent": 2,
                "max_files_allowed": 10,
            }
        )
        assert config.timeout == 15
        assert config.max_retries == 1
        assert config.max_concurrent == 2
        assert config.max_files_allowed == 10

    def test_from_dict_allowed_hosts_list_and_csv(self) -> None:
        from_list = GitLabConfig.from_dict({"allowed_hosts": ["gitlab.com", "GitLab.Example.COM"]})
        assert from_list.allowed_hosts == ("gitlab.com", "gitlab.example.com")
        from_csv = GitLabConfig.from_dict({"allowed_hosts": "gitlab.com, self-hosted.local"})
        assert from_csv.allowed_hosts == ("gitlab.com", "self-hosted.local")


@pytest.mark.unit
class TestGitLabConfigAllowedHosts:
    def test_default_is_gitlab_com_only(self) -> None:
        config = GitLabConfig()
        assert config.allowed_hosts == ("gitlab.com",)
        assert config.is_host_allowed("gitlab.com")
        assert config.is_host_allowed("GitLab.COM")
        assert not config.is_host_allowed("evil.internal")

    def test_is_host_allowed_strips_port(self) -> None:
        config = GitLabConfig(allowed_hosts=("gitlab.example.com",))
        assert config.is_host_allowed("gitlab.example.com:8443")
        assert not config.is_host_allowed("other.example.com:8443")

    def test_rejects_empty_or_invalid_hosts(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            GitLabConfig(allowed_hosts=())
        with pytest.raises(ValueError, match="bare hostnames"):
            GitLabConfig(allowed_hosts=("https://gitlab.com",))
        with pytest.raises(ValueError, match="bare hostnames"):
            GitLabConfig(allowed_hosts=("gitlab.com:443",))
