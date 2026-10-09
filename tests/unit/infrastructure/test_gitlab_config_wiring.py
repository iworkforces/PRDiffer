"""Tests for GitLabConfig wiring through SettingsService and settings.toml."""

from __future__ import annotations

import pytest

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.infrastructure.settings import SettingsService


@pytest.mark.unit
class TestGitLabConfigDefaults:
    def test_dataclass_defaults(self) -> None:
        config = GitLabConfig()
        assert config.timeout == 30
        assert config.pr_diff_request_timeout_seconds == 180.0
        assert config.max_file_size_bytes == 10_485_760
        assert config.max_files_allowed == 50
        assert config.max_concurrent == 4
        assert config.max_retries == 3
        assert config.retry_transient_errors is True
        assert config.obey_rate_limit is True
        assert config.allowed_hosts == ("gitlab.com",)


@pytest.mark.unit
class TestSettingsTomlGitLabDefaults:
    def test_real_settings_resolve_gitlab_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Blank env (not delenv): Dynaconf load_dotenv must not re-inject developer .env values.
        monkeypatch.setenv("GITLAB_ALLOWED_HOSTS", "")
        monkeypatch.setenv("MAX_FILES_ALLOWED", "")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_gitlab_config()
        assert config.timeout == 30
        assert config.pr_diff_request_timeout_seconds == 180.0
        assert config.max_file_size_bytes == 10_485_760
        assert config.max_files_allowed == 50
        assert config.max_concurrent == 4
        assert config.max_retries == 3
        assert config.retry_transient_errors is True
        assert config.obey_rate_limit is True
        assert config.allowed_hosts == ("gitlab.com",)

    def test_gitlab_config_is_cached_and_cleared(self) -> None:
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        first = service.get_gitlab_config()
        second = service.get_gitlab_config()
        assert first is second
        service.clear_cache()
        third = service.get_gitlab_config()
        assert third is not first
        assert third == first

    def test_gitlab_allowed_hosts_env_overrides_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GITLAB_ALLOWED_HOSTS (CSV) wins over settings.toml — used by .env / start script."""
        monkeypatch.setenv("GITLAB_ALLOWED_HOSTS", "gitlab.com, GitLab.Example.COM ,self-hosted.local")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_gitlab_config()
        assert config.allowed_hosts == ("gitlab.com", "gitlab.example.com", "self-hosted.local")

    def test_empty_gitlab_allowed_hosts_env_falls_back_to_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_ALLOWED_HOSTS", "   ")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_gitlab_config()
        assert config.allowed_hosts == ("gitlab.com",)

    def test_max_files_allowed_env_overrides_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAX_FILES_ALLOWED wins over settings.toml for GitLab — used by .env / start script."""
        monkeypatch.setenv("MAX_FILES_ALLOWED", "  25  ")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_gitlab_config()
        assert config.max_files_allowed == 25

    def test_empty_max_files_allowed_env_falls_back_to_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_FILES_ALLOWED", "   ")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_gitlab_config()
        assert config.max_files_allowed == 50
