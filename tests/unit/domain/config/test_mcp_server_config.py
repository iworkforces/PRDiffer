"""Unit coverage for MCP startup parsing and value-object invariants."""

from dataclasses import FrozenInstanceError
from collections.abc import Callable

import pytest

from prdiffer.domain.config.mcp_server_config import MCPServerConfig, TransportMode, parse_non_empty_text, parse_port, parse_transport
from prdiffer.domain.error_codes import E5009_CONFIGURATION_ERROR
from prdiffer.domain.exceptions import ConfigurationError


def construct_untrusted_config(constructor: Callable[..., MCPServerConfig], values: tuple[object, ...]) -> MCPServerConfig:
    return constructor(*values)


@pytest.mark.unit
class TestMCPServerConfig:
    @pytest.mark.parametrize("transport", ["stdio", "http", "sse", "streamable-http"])
    def test_transport_accepts_supported_names(self, transport: str) -> None:
        assert parse_transport(f" {transport} ", source="MCP_TRANSPORT") == transport

    @pytest.mark.parametrize("value", ["htp", "HTTP", "", "http\nsse", None, True, 9102, []])
    def test_transport_rejects_invalid_values(self, value: object) -> None:
        with pytest.raises(ConfigurationError) as caught:
            parse_transport(value, source="MCP_TRANSPORT")
        assert caught.value.error_code == E5009_CONFIGURATION_ERROR
        assert caught.value.details == {"setting": "transport", "source": "MCP_TRANSPORT"}
        assert len(str(caught.value).splitlines()) == 1

    @pytest.mark.parametrize("value,expected", [(1, 1), (65535, 65535), ("1", 1), ("65535", 65535), (" 9102 ", 9102), ("0" * 5000 + "1", 1)])
    def test_port_accepts_integer_and_ascii_digits(self, value: object, expected: int) -> None:
        assert parse_port(value, source="MCP_PORT") == expected

    @pytest.mark.parametrize("value", [0, -1, 65536, "abc", "", "9102.0", True, 9102.5, "-1", "+9102", "٩١٠٢", None, [], "9" * 5000])
    def test_port_rejects_invalid_values(self, value: object) -> None:
        with pytest.raises(ConfigurationError) as caught:
            parse_port(value, source="--port")
        assert caught.value.error_code == E5009_CONFIGURATION_ERROR
        assert caught.value.details == {"setting": "port", "source": "--port"}

    def test_error_message_is_bounded_and_single_line(self) -> None:
        with pytest.raises(ConfigurationError) as caught:
            parse_port("bad\n\r\x00" + "x" * 1000, source="--port")
        assert len(str(caught.value).splitlines()) == 1
        assert len(str(caught.value)) < 180
        assert "--port" in str(caught.value)

    @pytest.mark.parametrize("setting", ["host", "path"])
    def test_text_is_stripped(self, setting: str) -> None:
        assert parse_non_empty_text(" example ", setting=setting, source="default") == "example"

    @pytest.mark.parametrize("setting", ["host", "path"])
    @pytest.mark.parametrize("value", ["", " \t\n", None, 0, []])
    def test_text_requires_non_empty_string(self, setting: str, value: object) -> None:
        with pytest.raises(ConfigurationError) as caught:
            parse_non_empty_text(value, setting=setting, source=f"--{setting}")
        assert caught.value.error_code == E5009_CONFIGURATION_ERROR
        assert caught.value.details == {"setting": setting, "source": f"--{setting}"}

    @pytest.mark.parametrize("transport,port,is_stdio", [("stdio", None, True), ("http", 1, False), ("sse", 65535, False), ("streamable-http", 9102, False)])
    def test_valid_config_is_immutable(self, transport: TransportMode, port: int | None, is_stdio: bool) -> None:
        config = MCPServerConfig(transport, "127.0.0.1", port, "/mcp")
        assert config.is_stdio is is_stdio
        with pytest.raises(FrozenInstanceError):
            setattr(config, "host", "other")
        assert not hasattr(config, "__dict__")

    @pytest.mark.parametrize(
        "transport,host,port,path,setting",
        [
            ("htp", "host", 9102, "/mcp", "transport"),
            (None, "host", 9102, "/mcp", "transport"),
            ("stdio", "host", 9102, "/mcp", "port"),
            ("http", "host", None, "/mcp", "port"),
            ("http", "host", True, "/mcp", "port"),
            ("http", "host", "9102", "/mcp", "port"),
            ("http", "host", 9102.5, "/mcp", "port"),
            ("http", "host", 0, "/mcp", "port"),
            ("http", "host", 65536, "/mcp", "port"),
            ("http", " ", 9102, "/mcp", "host"),
            ("http", None, 9102, "/mcp", "host"),
            ("http", "host", 9102, "", "path"),
            ("stdio", "host", None, None, "path"),
        ],
    )
    def test_constructor_rejects_invalid_invariants(self, transport: object, host: object, port: object, path: object, setting: str) -> None:
        with pytest.raises(ConfigurationError) as caught:
            construct_untrusted_config(MCPServerConfig, (transport, host, port, path))
        assert caught.value.error_code == E5009_CONFIGURATION_ERROR
        assert caught.value.details == {"setting": setting, "source": "MCPServerConfig"}
