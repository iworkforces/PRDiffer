"""Tests for authoritative GitHubConfig wiring through settings and factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prdiffer.domain.config.github_config import GitHubConfig
from prdiffer.domain.exceptions import ConfigurationError
from prdiffer.infrastructure.factories.infrastructure_factory import InfrastructureFactory
from prdiffer.infrastructure.settings import SettingsService


@pytest.mark.unit
class TestGitHubConfigNewDefaults:
    def test_full_diff_defaults(self) -> None:
        config = GitHubConfig()
        assert config.timeout == 30
        assert config.pr_diff_request_timeout_seconds == 180.0
        assert config.max_file_size_bytes == 10_485_760
        assert config.max_total_chars == 600_000
        assert config.parallel_file_fetch_enabled is True
        assert config.parallel_diff_generation_enabled is True
        assert config.github_worker_capacity == 4

    def test_worker_capacity_when_parallel_enabled(self) -> None:
        config = GitHubConfig(parallel_file_fetch_enabled=True, max_concurrent=4)
        assert config.github_worker_capacity == 4

    def test_worker_capacity_when_parallel_disabled(self) -> None:
        config = GitHubConfig(parallel_file_fetch_enabled=False, max_concurrent=4)
        assert config.github_worker_capacity == 1

    def test_rejects_nonpositive_max_files(self) -> None:
        with pytest.raises(ConfigurationError, match="max_files_allowed"):
            GitHubConfig(max_files_allowed=0)

    def test_rejects_timeout_not_strictly_less_than_request(self) -> None:
        with pytest.raises(ConfigurationError, match="strictly less"):
            GitHubConfig(timeout=180, pr_diff_request_timeout_seconds=180)


@pytest.mark.unit
class TestSettingsTomlDefaults:
    def test_real_settings_resolve_timeouts_and_parallel_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Blank env (not delenv): Dynaconf load_dotenv must not re-inject developer .env values.
        monkeypatch.setenv("MAX_FILES_ALLOWED", "")
        monkeypatch.setenv("MAX_TOTAL_CHARS", "")
        monkeypatch.setenv("GITHUB_IGNORE_PATTERNS", "")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_github_config()
        assert config.timeout == 30
        assert config.pr_diff_request_timeout_seconds == 180.0
        assert config.max_file_size_bytes == 10_485_760
        assert config.max_total_chars == 600_000
        assert config.max_files_allowed == 50
        assert len(config.ignore_patterns) > 0
        assert "*.lock" in config.ignore_patterns
        assert config.parallel_file_fetch_enabled is True
        assert config.parallel_diff_generation_enabled is True
        assert config.github_worker_capacity == 4

    def test_max_files_allowed_env_overrides_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAX_FILES_ALLOWED wins over settings.toml — used by .env / start script."""
        monkeypatch.setenv("MAX_FILES_ALLOWED", "  12  ")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_github_config()
        assert config.max_files_allowed == 12
        assert service.get_app_settings()["max_files_allowed"] == 12

    def test_empty_max_files_allowed_env_falls_back_to_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_FILES_ALLOWED", "   ")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_github_config()
        assert config.max_files_allowed == 50

    def test_max_total_chars_env_overrides_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAX_TOTAL_CHARS wins over diff.max_total_chars — E5020 RESPONSE_SIZE_LIMIT budget."""
        monkeypatch.setenv("MAX_TOTAL_CHARS", "  800000  ")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_github_config()
        assert config.max_total_chars == 800_000

    def test_empty_max_total_chars_env_falls_back_to_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_TOTAL_CHARS", "   ")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_github_config()
        assert config.max_total_chars == 600_000

    def test_github_ignore_patterns_env_overrides_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GITHUB_IGNORE_PATTERNS (CSV) replaces settings.toml — used by .env / start script."""
        monkeypatch.setenv("GITHUB_IGNORE_PATTERNS", " *.lock , node_modules/ , dist/ , *AGENTS.md ")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_github_config()
        assert config.ignore_patterns == ("*.lock", "node_modules/", "dist/", "*AGENTS.md")
        assert service.get_github_settings()["ignore_patterns"] == ("*.lock", "node_modules/", "dist/", "*AGENTS.md")

    def test_github_ignore_patterns_agents_md_glob_applies_to_nested_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """*AGENTS.md from GITHUB_IGNORE_PATTERNS must drop nested AGENTS.md files."""
        from prdiffer.infrastructure.utils.pattern_matcher import PatternMatcher

        monkeypatch.setenv("GITHUB_IGNORE_PATTERNS", "*.lock,*AGENTS.md")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        matcher = PatternMatcher(list(service.get_github_config().ignore_patterns))
        assert matcher.is_valid_file("prdiffer/domain/AGENTS.md") is False
        assert matcher.is_valid_file("AGENTS.md") is False
        assert matcher.is_valid_file("prdiffer/domain/entities/file_content.py") is True

    def test_empty_github_ignore_patterns_env_falls_back_to_toml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_IGNORE_PATTERNS", "   ")
        service = SettingsService(settings_files=["settings.toml"])
        service.clear_cache()
        config = service.get_github_config()
        # settings.toml ships a non-empty default ignore list
        assert len(config.ignore_patterns) > 0
        assert "*.lock" in config.ignore_patterns


@pytest.mark.unit
class TestFactoryWiresExactSentinels:
    def test_create_pr_diff_service_receives_config_values(self) -> None:
        sentinel = GitHubConfig(
            timeout=15,
            pr_diff_request_timeout_seconds=90.0,
            max_file_size_bytes=1_000_000,
            max_total_chars=12_345,
            max_files_allowed=7,
            max_concurrent=3,
            parallel_file_fetch_enabled=False,
            parallel_diff_generation_enabled=False,
            large_file_threshold=111,
            chunk_size=222,
            max_diff_size=333,
        )

        factory = InfrastructureFactory()
        mock_settings = MagicMock()
        mock_settings.get_github_config.return_value = sentinel
        mock_settings.get_github_settings.return_value = {
            "ignore_patterns": (),
            "valid_extensions": (),
        }
        mock_settings.get.side_effect = lambda key, default=None: default

        with (
            patch(
                "prdiffer.infrastructure.factories.infrastructure_factory.get_settings_service",
                return_value=mock_settings,
            ),
            patch("prdiffer.infrastructure.services.pr_diff_service.get_settings_service", return_value=mock_settings),
        ):
            service = factory.create_pr_diff_service()

        assert service._diff_max_total_chars == 12_345
        assert service._pr_diff_request_timeout_seconds == 90.0
        assert service._github_timeout_seconds == 15
        assert service._file_processor is not None
        assert service._file_processor.max_files_allowed == 7
        assert service._file_processor._max_file_size_bytes == 1_000_000
        assert service._parallel_file_fetch_enabled is False
        assert service._max_concurrent == 1  # serialized github_worker_capacity
        assert service._get_session_reader()._limiter.total_tokens == 1

    def test_invalid_zero_max_files_fails_before_client(self) -> None:
        with pytest.raises(ConfigurationError, match="max_files_allowed"):
            GitHubConfig(max_files_allowed=0)
