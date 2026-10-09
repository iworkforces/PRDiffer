"""Real CLI, dotenv, Dynaconf and composition tests without binding a listener."""

import os
import sys

import pytest
from fastmcp import FastMCP

import prdiffer.infrastructure.cache.service as cache_module
import prdiffer.infrastructure.logging.console_logger as logging_module
from prdiffer import server as entrypoint


pytestmark = pytest.mark.integration
KEYS = ("transport", "host", "port", "path")
DEFAULT = ("http", "127.0.0.1", 9102, "/mcp")
SETTINGS = ("sse", "settings.test", 8101, "/settings")
DOTENV = ("streamable-http", "dotenv.test", 8102, "/dotenv")
PROCESS = ("http", "process.test", 8103, "/process")
CLI = ("sse", "cli.test", 8104, "/cli")


def settings_text(values, section="default"):
    fields = "\n".join(f"{key} = {value!r}" for key, value in zip(KEYS, values, strict=True))
    return f"[{section}.mcp]\n{fields}\n"


def dotenv_text(values):
    return "\n".join(f"MCP_{key.upper()}={value}" for key, value in zip(KEYS, values, strict=True)) + "\n"


@pytest.fixture
def startup(monkeypatch, tmp_path):
    # Given: all dotenv and Dynaconf reads are confined to this temporary project.
    monkeypatch.setattr("prdiffer.infrastructure.settings.project_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOT_PATH_FOR_DYNACONF", str(tmp_path))
    for key in tuple(os.environ):
        if key.startswith("MCP_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("MCP_AUTH_ENABLED", "false")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "settings.toml").write_text("[default]\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prdiffer"])
    calls = []
    servers = []
    real_create = entrypoint.create_mcp_server

    def record_run(self, **kwargs):
        calls.append(kwargs)

    def record_create(*args, **kwargs):
        built = real_create(*args, **kwargs)
        servers.append(built)
        assert built._mcp_config is kwargs["mcp_config"]
        assert built._server_configuration._mcp_config is kwargs["mcp_config"]
        return built

    monkeypatch.setattr(FastMCP, "run", record_run)
    monkeypatch.setattr(entrypoint, "create_mcp_server", record_create)
    return tmp_path, calls, servers


@pytest.mark.parametrize("layer", ["cli", "process", "dotenv", "settings", "defaults"])
def test_main_precedence(startup, monkeypatch, capsys, layer):
    root, calls, servers = startup
    expected = DEFAULT
    if layer != "defaults":
        (root / "settings.toml").write_text(settings_text(SETTINGS), encoding="utf-8")
        expected = SETTINGS
    if layer in ("cli", "process", "dotenv"):
        (root / ".env").write_text(dotenv_text(DOTENV), encoding="utf-8")
        expected = DOTENV
    if layer in ("cli", "process"):
        for key, value in zip(KEYS, PROCESS, strict=True):
            monkeypatch.setenv(f"MCP_{key.upper()}", str(value))
        expected = PROCESS
    if layer == "cli":
        monkeypatch.setattr(sys, "argv", ["prdiffer", *[part for key, value in zip(KEYS, CLI, strict=True) for part in (f"--{key}", str(value))]])
        expected = CLI
    # When: real startup loads .env, resolves settings and constructs the server.
    entrypoint.main()
    # Then: the actual run and information surface agree with independently chosen inputs.
    transport, host, port, path = expected
    assert calls == [{"transport": transport, "host": host, "port": port, "path": path, "uvicorn_config": {"proxy_headers": False}}]
    info = servers[0]._server_configuration.get_server_info()
    assert tuple(info[key] for key in KEYS) == expected
    assert f"Transport: {transport}" in capsys.readouterr().out
    if layer == "cli":
        assert tuple(os.environ[f"MCP_{key.upper()}"] for key in KEYS) == tuple(map(str, PROCESS))


@pytest.mark.parametrize("active", [True, False], ids=["testing", "default"])
def test_main_uses_active_dynaconf_environment(startup, monkeypatch, active):
    root, calls, servers = startup
    (root / "settings.toml").write_text(settings_text(SETTINGS) + settings_text(DOTENV, "testing"), encoding="utf-8")
    if active:
        monkeypatch.setenv("ENV_FOR_DYNACONF", "testing")
    else:
        monkeypatch.delenv("ENV_FOR_DYNACONF", raising=False)
    expected = DOTENV if active else SETTINGS
    entrypoint.main()
    transport, host, port, path = expected
    assert calls == [{"transport": transport, "host": host, "port": port, "path": path, "uvicorn_config": {"proxy_headers": False}}]
    assert tuple(servers[0]._server_configuration.get_server_info()[key] for key in KEYS) == expected


@pytest.mark.parametrize("transport", ["http", "sse", "streamable-http", "stdio"])
@pytest.mark.parametrize("source", ["dotenv", "settings"])
def test_main_transport_and_diagnostic_stream(startup, capsys, transport, source):
    root, calls, servers = startup
    values = (transport, "transport.test", 8234, "/transport")
    if source == "dotenv":
        (root / ".env").write_text(dotenv_text(values), encoding="utf-8")
    else:
        (root / "settings.toml").write_text(settings_text(values), encoding="utf-8")
    entrypoint.main()
    info = servers[0]._server_configuration.get_server_info()
    captured = capsys.readouterr()
    assert tuple(info[key] for key in KEYS) == (transport, "transport.test", None if transport == "stdio" else 8234, "/transport")
    if transport == "stdio":
        assert calls == [{"transport": "stdio"}]
        assert captured.out == ""
        assert "Transport: stdio" in captured.err
        assert "Loaded environment from" in captured.err
        assert "Running MCP server" in captured.err
    else:
        assert calls == [{"transport": transport, "host": "transport.test", "port": 8234, "path": "/transport", "uvicorn_config": {"proxy_headers": False}}]


@pytest.mark.parametrize("source", ["cli", "process", "dotenv", "settings"])
@pytest.mark.parametrize("key,value", [("transport", "stdo"), ("port", "abc"), ("port", "0"), ("port", "-1"), ("port", "65536")])
def test_main_rejects_invalid_configuration_before_initialization(startup, monkeypatch, capsys, source, key, value):
    root, calls, servers = startup
    # Given: a valid fallback must not mask an explicitly invalid input.
    (root / "settings.toml").write_text(settings_text(DEFAULT), encoding="utf-8")
    if key == "transport":
        monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    label = f"MCP_{key.upper()}"
    if source == "cli":
        monkeypatch.setattr(sys, "argv", ["prdiffer", f"--{key}", value])
        label = f"--{key}"
    elif source == "process":
        monkeypatch.setenv(label, value)
    elif source == "dotenv":
        monkeypatch.delenv(label, raising=False)
        (root / ".env").write_text(f"{label}={value}\n", encoding="utf-8")
    else:
        monkeypatch.delenv(label, raising=False)
        values = list(DEFAULT)
        values[KEYS.index(key)] = value
        (root / "settings.toml").write_text(settings_text(values), encoding="utf-8")
        label = f"settings mcp.{key}"
    # When / Then: one diagnostic, no banner, logger, cache or server construction.
    with pytest.raises(SystemExit) as raised:
        entrypoint.main()
    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert "E5009_CONFIGURATION_ERROR" in captured.err
    assert label in captured.err
    assert "Traceback" not in captured.err
    assert calls == []
    assert servers == []
    assert logging_module._logger_instance is None
    assert cache_module._cache_service is None
    if source == "process" and key == "port" and value == "abc":
        assert captured.err == "❌ E5009_CONFIGURATION_ERROR: Invalid MCP port 'abc' from MCP_PORT; expected an integer between 1 and 65535\n"


@pytest.mark.parametrize("port", [0, 70000])
def test_main_rejects_integer_settings_port(startup, capsys, port):
    root, calls, servers = startup
    (root / "settings.toml").write_text(settings_text(("http", "127.0.0.1", port, "/mcp")), encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        entrypoint.main()
    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert "E5009_CONFIGURATION_ERROR" in captured.err
    assert "settings mcp.port" in captured.err
    assert "Traceback" not in captured.err
    assert calls == servers == []
