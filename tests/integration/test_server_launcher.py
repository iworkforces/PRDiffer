from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

import pytest


LAUNCHER = Path(__file__).resolve().parents[2] / "start-prdiffer-mcp-server.sh"
pytestmark = pytest.mark.integration


def immutable_prefix(project: LauncherProject) -> list[str]:
    return [
        "run",
        "--project",
        str(project.root),
        "--frozen",
        "--no-sync",
        "--no-python-downloads",
        "--no-env-file",
    ]


@dataclass(frozen=True, slots=True)
class LauncherProject:
    root: Path
    script: Path
    uv_log: Path
    preflight_log: Path
    argv_log: Path
    token_log: Path
    pid_file: Path
    uv_marker: Path

    def run(
        self,
        args: Sequence[str] = (),
        *,
        extra_env: Mapping[str, str] | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "ARGV_LOG": str(self.argv_log),
            "ENV_FILE": str(self.root / "missing.env"),
            "FAKE_CHILD_MODE": "normal",
            "GITHUB_TOKEN": "github-placeholder",
            "GITLAB_TOKEN": "",
            "HOME": str(self.root),
            "LC_ALL": "C",
            "PATH": f"{self.root / 'bin'}:/usr/bin:/bin",
            "PID_FILE": str(self.pid_file),
            "PREFLIGHT_LOG": str(self.preflight_log),
            "TOKEN_LOG": str(self.token_log),
            "UV_LOG": str(self.uv_log),
            "UV_MARKER": str(self.uv_marker),
        }
        if extra_env is not None:
            environment.update(extra_env)
        return subprocess.run(
            ["/bin/bash", str(self.script), *args],
            capture_output=True,
            check=False,
            cwd=self.root,
            env=environment,
            input=input_text,
            text=True,
            timeout=6,
        )


@pytest.fixture
def launcher_project(tmp_path: Path) -> LauncherProject:
    script = tmp_path / LAUNCHER.name
    shutil.copy2(LAUNCHER, script)
    (tmp_path / "prdiffer").mkdir()
    (tmp_path / "prdiffer" / "server.py").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/bin/bash
set -euo pipefail
: > "$UV_MARKER"
{
    printf '%s\n' '---'
    printf '%s\n' "$@"
} >> "$UV_LOG"

if [[ $# -eq 9 && "$8" == "python" && "$9" == "--version" ]]; then
    printf '%s\n' "$@" > "$PREFLIGHT_LOG"
    exit 0
fi

if [[ $# -ge 9 && "$8" == "python" ]]; then
    printf '%s\n' "$@" > "$ARGV_LOG"
    printf '%s|%s\n' "${GITHUB_TOKEN:-}" "${GITLAB_TOKEN:-}" > "$TOKEN_LOG"
    printf '%s\n' "child diagnostic" >&2
    case "${FAKE_CHILD_MODE:-normal}" in
        normal)
            /bin/sleep 1.2
            ;;
        stdio)
            IFS= read -r input_line
            printf 'child:%s\n' "$input_line"
            /bin/sleep 1.2
            ;;
        nonzero)
            exit "${CHILD_STATUS:-23}"
            ;;
        overwrite-pid)
            /bin/sleep 1.05
            printf '%s\n' "777777" > "$PID_FILE"
            /bin/sleep 0.15
            ;;
        *)
            exit 99
            ;;
    esac
    exit 0
fi

exit 98
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    fake_nc = fake_bin / "nc"
    fake_nc.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    fake_nc.chmod(0o755)

    return LauncherProject(
        root=tmp_path,
        script=script,
        uv_log=tmp_path / "uv.log",
        preflight_log=tmp_path / "preflight.log",
        argv_log=tmp_path / "argv.log",
        token_log=tmp_path / "token.log",
        pid_file=tmp_path / "server.pid",
        uv_marker=tmp_path / "uv.marker",
    )


def test_invalid_cli_has_no_side_effects(launcher_project: LauncherProject) -> None:
    env_marker = launcher_project.root / "env-side-effect"
    injection_marker = launcher_project.root / "injected"
    env_file = launcher_project.root / ".env"
    env_file.write_text(': > "$ENV_SIDE_EFFECT"\n', encoding="utf-8")
    launcher_project.pid_file.write_text("existing-owner\n", encoding="utf-8")
    environment = {"ENV_FILE": str(env_file), "ENV_SIDE_EFFECT": str(env_marker)}
    invalid_arguments: list[tuple[str, ...]] = [
        ("--transport",),
        ("--port",),
        ("--unknown",),
        ("positional",),
        ("--transport", "websocket"),
        ("--port", "0"),
        ("--port", "65536"),
        ("--port", "12x"),
        ("--port", f"9102; /usr/bin/touch {injection_marker}"),
        ("--port", f"9102; /usr/bin/touch {injection_marker}", "--port", "9102"),
        ("--transport", "invalid", "--transport", "http"),
        ("--help", "--unknown"),
    ]

    for arguments in invalid_arguments:
        completed = launcher_project.run(arguments, extra_env=environment)
        assert completed.returncode != 0
        assert "--help" in completed.stderr
        assert launcher_project.pid_file.read_text(encoding="utf-8") == "existing-owner\n"
        assert not env_marker.exists()
        assert not injection_marker.exists()
        assert not launcher_project.uv_marker.exists()


def test_help_has_no_side_effects(launcher_project: LauncherProject) -> None:
    launcher_project.pid_file.write_text("existing-owner\n", encoding="utf-8")
    completed = launcher_project.run(["--help"])

    assert completed.returncode == 0
    assert "Usage:" in completed.stderr
    assert launcher_project.pid_file.read_text(encoding="utf-8") == "existing-owner\n"
    assert not launcher_project.uv_marker.exists()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        pytest.param("TRANSPORT", "", id="empty-transport"),
        pytest.param("TRANSPORT", "stdio; exit 0", id="invalid-transport"),
        pytest.param("PORT", "", id="empty-port"),
        pytest.param("PORT", "70000", id="out-of-range-port"),
        pytest.param("PORT", "９１０２", id="non-ascii-port"),
    ],
)
def test_invalid_ambient_selection_has_no_side_effects(
    launcher_project: LauncherProject,
    name: str,
    value: str,
) -> None:
    launcher_project.pid_file.write_text("existing-owner\n", encoding="utf-8")
    completed = launcher_project.run(
        ["--transport", "http", "--port", "9102"],
        extra_env={name: value},
    )

    assert completed.returncode != 0
    assert "--help" in completed.stderr
    assert launcher_project.pid_file.read_text(encoding="utf-8") == "existing-owner\n"
    assert not launcher_project.uv_marker.exists()


def test_default_http_uses_exact_immutable_argv(launcher_project: LauncherProject) -> None:
    completed = launcher_project.run()
    expected_prefix = immutable_prefix(launcher_project)

    assert completed.returncode == 0
    assert launcher_project.preflight_log.read_text(encoding="utf-8").splitlines() == [*expected_prefix, "python", "--version"]
    assert launcher_project.argv_log.read_text(encoding="utf-8").splitlines() == [
        *expected_prefix,
        "python",
        str(launcher_project.root / "prdiffer" / "server.py"),
        "--transport",
        "http",
        "--port",
        "9102",
    ]
    invocations = launcher_project.uv_log.read_text(encoding="utf-8").splitlines()
    assert "sync" not in invocations
    assert "install" not in invocations
    assert not launcher_project.pid_file.exists()


def test_stdio_is_protocol_clean_and_bidirectional(launcher_project: LauncherProject) -> None:
    completed = launcher_project.run(
        ["--transport", "stdio", "--port", "1234"],
        extra_env={"FAKE_CHILD_MODE": "stdio"},
        input_text="request-payload\n",
    )

    assert completed.returncode == 0
    assert completed.stdout == "child:request-payload\n"
    assert "PRDiffer MCP Server" in completed.stderr
    assert "child diagnostic" in completed.stderr
    assert launcher_project.argv_log.read_text(encoding="utf-8").splitlines() == [
        *immutable_prefix(launcher_project),
        "python",
        str(launcher_project.root / "prdiffer" / "server.py"),
        "--transport",
        "stdio",
    ]
    assert "--port" not in launcher_project.argv_log.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    ("github_token", "gitlab_token", "expects_launch"),
    [
        pytest.param("", "gitlab-placeholder", True, id="gitlab-only"),
        pytest.param("github-placeholder", "gitlab-placeholder", True, id="both"),
        pytest.param("", "", False, id="neither"),
    ],
)
def test_provider_token_gate(
    launcher_project: LauncherProject,
    github_token: str,
    gitlab_token: str,
    expects_launch: bool,
) -> None:
    completed = launcher_project.run(extra_env={"GITHUB_TOKEN": github_token, "GITLAB_TOKEN": gitlab_token})

    assert (launcher_project.argv_log.exists()) is expects_launch
    assert completed.returncode == (0 if expects_launch else 1)


def test_env_token_loads_without_overriding_cli_selection(launcher_project: LauncherProject) -> None:
    env_file = launcher_project.root / ".env"
    env_file.write_text(
        "GITHUB_TOKEN=from-env\nTRANSPORT=sse\nPORT=9200\n",
        encoding="utf-8",
    )
    completed = launcher_project.run(
        ["--transport", "streamable-http", "--port", "9300"],
        extra_env={"ENV_FILE": str(env_file), "GITHUB_TOKEN": "", "TRANSPORT": "http", "PORT": "9102"},
    )

    assert completed.returncode == 0
    assert launcher_project.token_log.read_text(encoding="utf-8") == "from-env|\n"
    assert launcher_project.argv_log.read_text(encoding="utf-8").splitlines()[-4:] == [
        "--transport",
        "streamable-http",
        "--port",
        "9300",
    ]


def test_malformed_stale_pid_is_never_executed(launcher_project: LauncherProject) -> None:
    injection_marker = launcher_project.root / "pid-injected"
    launcher_project.pid_file.write_text(f"$(/usr/bin/touch {injection_marker})\n", encoding="utf-8")

    completed = launcher_project.run()

    assert completed.returncode == 0
    assert not injection_marker.exists()
    assert not launcher_project.pid_file.exists()


def test_cleanup_leaves_pid_file_it_no_longer_owns(launcher_project: LauncherProject) -> None:
    completed = launcher_project.run(extra_env={"FAKE_CHILD_MODE": "overwrite-pid"})

    assert completed.returncode == 0
    assert launcher_project.pid_file.read_text(encoding="utf-8") == "777777\n"


def test_child_nonzero_status_is_preserved_and_reaped(launcher_project: LauncherProject) -> None:
    completed = launcher_project.run(extra_env={"FAKE_CHILD_MODE": "nonzero", "CHILD_STATUS": "23"})

    assert completed.returncode == 23
    assert not launcher_project.pid_file.exists()
