"""Validated MCP startup configuration.

Frozen, slotted value object and pure parsers for transport and bind settings.
Configuration sources are selected by the application layer, never read here.
"""

from dataclasses import dataclass
from typing import Literal, TypeAlias

from prdiffer.domain.error_codes import E5009_CONFIGURATION_ERROR
from prdiffer.domain.exceptions import ConfigurationError

TransportMode: TypeAlias = Literal["stdio", "http", "sse", "streamable-http"]
SUPPORTED_TRANSPORTS: tuple[TransportMode, ...] = ("stdio", "http", "sse", "streamable-http")
DEFAULT_TRANSPORT: TransportMode = "http"
DEFAULT_PORT = 9102
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PATH = "/mcp"


def _render_value(value: object) -> str:
    """Render a bounded ASCII representation safe for one-line diagnostics."""
    rendered = ascii(value)
    return rendered if len(rendered) <= 64 else rendered[:61] + "..."


def _invalid(value: object, *, setting: str, source: str, expected: str) -> ConfigurationError:
    """Build the shared structured startup-configuration error."""
    safe_source = source.encode("unicode_escape").decode("ascii")
    return ConfigurationError(
        f"Invalid MCP {setting} {_render_value(value)} from {safe_source}; expected {expected}",
        error_code=E5009_CONFIGURATION_ERROR,
        details={"setting": setting, "source": source},
    )


def parse_transport(value: object, *, source: str) -> TransportMode:
    """Parse a case-sensitive transport name, stripping surrounding whitespace."""
    if isinstance(value, str):
        normalized = value.strip()
        for transport in SUPPORTED_TRANSPORTS:
            if normalized == transport:
                return transport
    raise _invalid(value, setting="transport", source=source, expected="one of " + ", ".join(SUPPORTED_TRANSPORTS))


def parse_port(value: object, *, source: str) -> int:
    """Parse an integer or ASCII decimal string as a network port in 1..65535."""
    error = _invalid(value, setting="port", source=source, expected="an integer between 1 and 65535")
    if isinstance(value, bool):
        raise error
    if isinstance(value, int):
        port = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized or not normalized.isascii() or not normalized.isdigit():
            raise error
        significant_digits = normalized.lstrip("0") or "0"
        if len(significant_digits) > 5:
            raise error
        port = int(significant_digits)
    else:
        raise error
    if not 1 <= port <= 65535:
        raise error
    return port


def parse_non_empty_text(value: object, *, source: str, setting: str) -> str:
    """Parse a host or path as non-empty text with surrounding whitespace removed."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise _invalid(value, setting=setting, source=source, expected="non-empty text")


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """Immutable effective MCP transport and bind settings."""

    transport: TransportMode
    host: str
    port: int | None
    path: str

    def __post_init__(self) -> None:
        source = "MCPServerConfig"
        if self.transport not in SUPPORTED_TRANSPORTS:
            raise _invalid(self.transport, setting="transport", source=source, expected="one of " + ", ".join(SUPPORTED_TRANSPORTS))
        if self.is_stdio:
            if self.port is not None:
                raise _invalid(self.port, setting="port", source=source, expected="None for stdio")
        elif not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise _invalid(self.port, setting="port", source=source, expected="an integer between 1 and 65535")
        parse_non_empty_text(self.host, source=source, setting="host")
        parse_non_empty_text(self.path, source=source, setting="path")

    @property
    def is_stdio(self) -> bool:
        """True when the effective transport does not bind a network port."""
        return self.transport == "stdio"
