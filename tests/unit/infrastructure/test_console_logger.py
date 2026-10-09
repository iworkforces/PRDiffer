def test_console_logger_uses_stderr_in_stdio(monkeypatch, capsys):
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")

    import prdiffer.infrastructure.logging.console_logger as logger_module

    logger_module._logger_instance = None
    logger = logger_module.ConsoleLogger()

    logger.info("stdio-log-test")
    captured = capsys.readouterr()

    assert "stdio-log-test" in captured.err
    assert captured.out == ""

    # Clean up env for other tests
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)


def test_explicit_stdio_beats_http_environment_and_settings(monkeypatch, capsys):
    from unittest.mock import Mock
    from prdiffer.infrastructure.logging import console_logger as logger_module

    monkeypatch.setenv("MCP_TRANSPORT", "http")
    settings = Mock()
    settings.get_app_settings.return_value = {"logging_enabled": True, "log_level": "INFO"}
    settings.get.side_effect = lambda key, default=None: {"mcp.transport": "http", "app.logging_enabled": True, "app.log_level": "INFO"}.get(key, default)
    monkeypatch.setattr(logger_module, "get_settings_service", lambda: settings)
    logger = logger_module.ConsoleLogger(transport="stdio")
    logger.info("explicit-stdio")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "explicit-stdio" in captured.err


def test_explicit_transport_switches_existing_singleton_without_resetting_handlers(capsys):
    import logging
    from prdiffer.infrastructure.logging import console_logger as logger_module

    original = logger_module.get_logger(transport="http")
    handlers = tuple(logging.getLogger().handlers)
    logger = logger_module.get_logger(transport="stdio")
    logger.info("switched-stdio")
    captured = capsys.readouterr()
    assert logger is original
    assert tuple(logging.getLogger().handlers) == handlers
    assert captured.out == ""
    assert "switched-stdio" in captured.err
