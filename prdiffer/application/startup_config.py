"""Resolve MCP startup values once, before server initialization."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from prdiffer.domain.config.mcp_server_config import (
    DEFAULT_HOST,
    DEFAULT_PATH,
    DEFAULT_PORT,
    DEFAULT_TRANSPORT,
    MCPServerConfig,
    parse_non_empty_text,
    parse_port,
    parse_transport,
)
from prdiffer.domain.services.settings import SettingsServiceInterface


@dataclass(frozen=True, slots=True)
class StartupOverrides:
    """Explicit CLI values; None means the corresponding flag was not supplied."""

    transport: str | None = None
    port: str | int | None = None
    host: str | None = None
    path: str | None = None


def resolve_mcp_server_config(
    settings: SettingsServiceInterface,
    *,
    overrides: StartupOverrides | None = None,
    environ: Mapping[str, str] | None = None,
) -> MCPServerConfig:
    """Resolve CLI > environment > raw settings > defaults, then parse values.

    Missing or blank environment values count as unset. Explicit CLI values,
    including empty text and zero, are never replaced by lower-priority values.
    The process environment is read at call time when environ is omitted.
    Stdio ignores the port entirely, without reading any port source.
    """
    cli = overrides if overrides is not None else StartupOverrides()
    environment = os.environ if environ is None else environ

    def select(setting: str, override: object, default: object) -> tuple[object, str]:
        if override is not None:
            return override, f"--{setting}"
        env_key = f"MCP_{setting.upper()}"
        env_value = environment.get(env_key)
        if env_value is not None and env_value.strip():
            return env_value, env_key
        settings_key = f"mcp.{setting}"
        settings_value = settings.get(settings_key)
        if settings_value is not None:
            return settings_value, f"settings {settings_key}"
        return default, "default"

    transport_value, transport_source = select("transport", cli.transport, DEFAULT_TRANSPORT)
    transport = parse_transport(transport_value, source=transport_source)
    port = None
    if transport != "stdio":
        port_value, port_source = select("port", cli.port, DEFAULT_PORT)
        port = parse_port(port_value, source=port_source)
    host_value, host_source = select("host", cli.host, DEFAULT_HOST)
    path_value, path_source = select("path", cli.path, DEFAULT_PATH)
    return MCPServerConfig(
        transport=transport,
        host=parse_non_empty_text(host_value, source=host_source, setting="host"),
        port=port,
        path=parse_non_empty_text(path_value, source=path_source, setting="path"),
    )
