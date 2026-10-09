import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any
from prdiffer.domain.config.mcp_server_config import TransportMode
from prdiffer.infrastructure.settings import get_settings_service
from prdiffer.domain.services.logger import LoggerServiceInterface, LogLevel


class ConsoleLogger(LoggerServiceInterface):
    """Console-based logging service with colored text and JSON output formats.

    Transport-aware: forces stderr in stdio mode to prevent JSON-RPC corruption.
    """

    COLORS = {
        LogLevel.DEBUG: "\033[36m",  # Cyan
        LogLevel.INFO: "\033[32m",  # Green
        LogLevel.WARNING: "\033[33m",  # Yellow
        LogLevel.ERROR: "\033[31m",  # Red
        LogLevel.CRITICAL: "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def __init__(self, transport: TransportMode | None = None):
        self.settings_service = get_settings_service()
        self._configure_logger(transport)

    def use_transport(self, transport: TransportMode) -> None:
        """Select the diagnostic stream without replacing root logging handlers."""
        self._force_stderr = transport == "stdio"

    def _configure_logger(self, transport: TransportMode | None = None) -> None:
        app_settings = self.settings_service.get_app_settings()
        self.enabled = app_settings.get("logging_enabled", True)

        log_level_str = app_settings.get("log_level", "INFO").upper()
        self.log_level = getattr(LogLevel, log_level_str, LogLevel.INFO)

        selected_transport = transport if transport is not None else os.getenv("MCP_TRANSPORT") or self.settings_service.get("mcp.transport", "stdio")
        self._force_stderr = selected_transport == "stdio"

        self._log_format = self.settings_service.get("logging.format", "simple")
        self._json_pretty = self.settings_service.get("logging.json_pretty", False)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=sys.stderr if self._force_stderr else sys.stdout,
        )

    def should_log(self, level: LogLevel) -> bool:
        if not self.enabled:
            return False

        level_values = {
            LogLevel.DEBUG: 10,
            LogLevel.INFO: 20,
            LogLevel.WARNING: 30,
            LogLevel.ERROR: 40,
            LogLevel.CRITICAL: 50,
        }

        current_level_value = level_values.get(self.log_level, 20)
        message_level_value = level_values.get(level, 20)

        return message_level_value >= current_level_value

    def is_enabled_for(self, level: str) -> bool:
        log_level = getattr(LogLevel, level.upper(), None)
        if log_level is None:
            return False
        return self.should_log(log_level)

    def debug(self, message: str, **kwargs: object) -> None:
        if self.should_log(LogLevel.DEBUG):
            self._log(LogLevel.DEBUG, message, kwargs)

    def info(self, message: str, **kwargs: object) -> None:
        if self.should_log(LogLevel.INFO):
            self._log(LogLevel.INFO, message, kwargs)

    def warning(self, message: str, **kwargs: object) -> None:
        if self.should_log(LogLevel.WARNING):
            self._log(LogLevel.WARNING, message, kwargs)

    def error(self, message: str, **kwargs: object) -> None:
        if self.should_log(LogLevel.ERROR):
            self._log(LogLevel.ERROR, message, kwargs)

    def critical(self, message: str, **kwargs: object) -> None:
        if self.should_log(LogLevel.CRITICAL):
            self._log(LogLevel.CRITICAL, message, kwargs)

    def _log(self, level: LogLevel, message: str, context: dict[str, Any]) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        color = self.COLORS.get(level, self.RESET)

        if self._log_format == "json":
            self._log_json(level, message, context, timestamp)
        else:
            self._log_text(level, message, context, timestamp, color)

    def _get_stream(self, level: LogLevel):
        if self._force_stderr or level in (LogLevel.ERROR, LogLevel.CRITICAL):
            return sys.stderr
        return sys.stdout

    def _log_text(
        self,
        level: LogLevel,
        message: str,
        context: dict[str, Any],
        timestamp: str,
        color: str,
    ) -> None:
        if context:
            context_str = " ".join([f"{k}={v}" for k, v in context.items()])
            formatted_message = f"{message} [{context_str}]"
        else:
            formatted_message = message

        log_line = f"{color}{timestamp} - {level.value} - {formatted_message}{self.RESET}"

        print(log_line, file=self._get_stream(level))

    def _log_json(self, level: LogLevel, message: str, context: dict[str, Any], timestamp: str) -> None:
        log_record: dict[str, Any] = {
            "timestamp": timestamp,
            "level": level.value,
            "message": message,
            "context": context,
        }

        stream = self._get_stream(level)

        if self._json_pretty:
            print(json.dumps(log_record, indent=2), file=stream)
        else:
            print(json.dumps(log_record), file=stream)


_logger_instance: ConsoleLogger | None = None


def get_logger(transport: TransportMode | None = None) -> ConsoleLogger:
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = ConsoleLogger(transport=transport)
    elif transport is not None:
        _logger_instance.use_transport(transport)
    return _logger_instance
