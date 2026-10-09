"""Unit coverage for startup precedence without Dynaconf or dotenv I/O."""

from collections.abc import Iterator, Mapping

import pytest

from prdiffer.application.startup_config import StartupOverrides, resolve_mcp_server_config
from prdiffer.domain.config.mcp_server_config import MCPServerConfig
from prdiffer.domain.error_codes import E5009_CONFIGURATION_ERROR
from prdiffer.domain.exceptions import ConfigurationError
from prdiffer.domain.services.settings import SettingsServiceInterface


class FakeSettings(SettingsServiceInterface):
    def __init__(self, values: Mapping[str, object]) -> None:
        self.values = values
        self.read_keys: list[str] = []

    def get(self, key: str, default: object = None) -> object:
        self.read_keys.append(key)
        return self.values.get(key, default)

    def get_cache_settings(self) -> dict[str, object]:
        raise NotImplementedError

    def get_app_settings(self) -> dict[str, object]:
        raise NotImplementedError

    def clear_cache(self) -> None:
        raise NotImplementedError


@pytest.mark.unit
class TestStartupConfig:
    def test_defaults_when_all_sources_unset(self) -> None:
        config = resolve_mcp_server_config(FakeSettings({}), environ={})
        assert config == MCPServerConfig("http", "127.0.0.1", 9102, "/mcp")

    def test_http_settings_remain_default(self) -> None:
        config = resolve_mcp_server_config(FakeSettings({"mcp.transport": "http"}), environ={})
        assert config.transport == "http"

    @pytest.mark.parametrize("level", ["cli", "env", "settings", "default"])
    @pytest.mark.parametrize(
        "setting,cli_value,env_value,settings_value,default_value",
        [
            ("transport", "streamable-http", "sse", "stdio", "http"),
            ("port", "9201", "9202", 9203, 9102),
            ("host", "cli.host", "env.host", "settings.host", "127.0.0.1"),
            ("path", "/cli", "/env", "/settings", "/mcp"),
        ],
    )
    def test_precedence_for_each_field(
        self, level: str, setting: str, cli_value: str, env_value: str, settings_value: str | int, default_value: str | int
    ) -> None:
        settings = FakeSettings({f"mcp.{setting}": settings_value} if level != "default" else {})
        environ = {f"MCP_{setting.upper()}": env_value} if level in ("cli", "env") else {}
        overrides = (
            StartupOverrides(
                transport=cli_value if setting == "transport" else None,
                port=cli_value if setting == "port" else None,
                host=cli_value if setting == "host" else None,
                path=cli_value if setting == "path" else None,
            )
            if level == "cli"
            else None
        )
        expected_values: dict[str, str | int] = {"cli": cli_value, "env": env_value, "settings": settings_value, "default": default_value}
        expected = expected_values[level]
        config = resolve_mcp_server_config(settings, overrides=overrides, environ=environ)
        assert getattr(config, setting) == (int(expected) if setting == "port" else expected)

    @pytest.mark.parametrize("blank", ["", " \t\n"])
    @pytest.mark.parametrize("setting,value", [("transport", "sse"), ("port", 9200), ("host", "settings.host"), ("path", "/settings")])
    def test_blank_environment_falls_through(self, blank: str, setting: str, value: str | int) -> None:
        config = resolve_mcp_server_config(FakeSettings({f"mcp.{setting}": value}), environ={f"MCP_{setting.upper()}": blank})
        assert getattr(config, setting) == value

    def test_padded_environment_port_is_accepted(self) -> None:
        config = resolve_mcp_server_config(FakeSettings({"mcp.port": 9200}), environ={"MCP_PORT": " 9102 "})
        assert config.port == 9102

    @pytest.mark.parametrize(
        "overrides,environ,values,setting,source",
        [
            (StartupOverrides(port=0), {"MCP_PORT": "9200"}, {"mcp.port": 9102}, "port", "--port"),
            (None, {"MCP_PORT": "0"}, {"mcp.port": 9102}, "port", "MCP_PORT"),
            (None, {}, {"mcp.port": "nope"}, "port", "settings mcp.port"),
            (StartupOverrides(transport=""), {"MCP_TRANSPORT": "http"}, {}, "transport", "--transport"),
            (StartupOverrides(port=""), {"MCP_PORT": "9102"}, {}, "port", "--port"),
            (StartupOverrides(host=""), {"MCP_HOST": "host"}, {}, "host", "--host"),
            (StartupOverrides(path=""), {"MCP_PATH": "/mcp"}, {}, "path", "--path"),
            (None, {"MCP_TRANSPORT": "htp\nsse"}, {}, "transport", "MCP_TRANSPORT"),
            (None, {}, {"mcp.transport": False}, "transport", "settings mcp.transport"),
            (None, {}, {"mcp.host": 42}, "host", "settings mcp.host"),
            (None, {}, {"mcp.path": ""}, "path", "settings mcp.path"),
        ],
    )
    def test_invalid_selected_source_is_not_replaced(
        self, overrides: StartupOverrides | None, environ: Mapping[str, str], values: Mapping[str, object], setting: str, source: str
    ) -> None:
        with pytest.raises(ConfigurationError) as caught:
            resolve_mcp_server_config(FakeSettings(values), overrides=overrides, environ=environ)
        assert caught.value.error_code == E5009_CONFIGURATION_ERROR
        assert caught.value.details == {"setting": setting, "source": source}
        assert source in str(caught.value)
        assert len(str(caught.value).splitlines()) == 1

    @pytest.mark.parametrize("source", ["cli", "env", "settings"])
    def test_stdio_ignores_invalid_ports_from_all_sources(self, source: str) -> None:
        settings = FakeSettings({"mcp.port": "bad", **({"mcp.transport": "stdio"} if source == "settings" else {})})
        environ = {"MCP_PORT": "bad", **({"MCP_TRANSPORT": "stdio"} if source == "env" else {})}
        overrides = StartupOverrides(transport="stdio" if source == "cli" else None, port="bad")
        config = resolve_mcp_server_config(settings, overrides=overrides, environ=environ)
        assert config.port is None
        assert "mcp.port" not in settings.read_keys

    def test_stdio_does_not_read_environment_port(self) -> None:
        class PortUnreadableEnvironment(Mapping[str, str]):
            def __getitem__(self, key: str) -> str:
                assert key != "MCP_PORT"
                if key == "MCP_TRANSPORT":
                    return "stdio"
                raise KeyError(key)

            def __iter__(self) -> Iterator[str]:
                return iter(("MCP_TRANSPORT",))

            def __len__(self) -> int:
                return 1

        config = resolve_mcp_server_config(FakeSettings({}), environ=PortUnreadableEnvironment())
        assert config.port is None

    def test_environment_is_read_at_call_time_without_mutation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("MCP_TRANSPORT", "MCP_PORT", "MCP_HOST", "MCP_PATH"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("MCP_TRANSPORT", "sse")
        monkeypatch.setenv("MCP_PORT", "9300")
        config = resolve_mcp_server_config(FakeSettings({}))
        assert config.transport == "sse"
        assert config.port == 9300

    def test_none_settings_values_use_defaults(self) -> None:
        config = resolve_mcp_server_config(FakeSettings({f"mcp.{field}": None for field in ("transport", "port", "host", "path")}), environ={})
        assert config == MCPServerConfig("http", "127.0.0.1", 9102, "/mcp")
