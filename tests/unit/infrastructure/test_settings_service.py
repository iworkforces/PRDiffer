from prdiffer.infrastructure.settings import SettingsService


def test_get_github_config_includes_advanced_keys():
    config = SettingsService().get_github_config()

    assert config.retry_on_404 is False
    assert config.retry_on_403 is True
    assert config.retry_on_500 is True
    assert config.circuit_breaker_enabled is True
    assert config.diff_max_workers == 4
    assert isinstance(config.ignore_patterns, tuple)
    assert isinstance(config.valid_extensions, tuple)
