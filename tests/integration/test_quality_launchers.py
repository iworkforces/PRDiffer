from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Final

import pytest


REPOSITORY: Final = Path(__file__).resolve().parents[2]
WRAPPERS: Final = ("start-lint.sh", "start-type-check.sh", "start-unittest.sh")
pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class QualitySandbox:
    root: Path
    home: Path
    bin_dir: Path
    log: Path
    installer_log: Path
    profile_marker: Path

    def invoke(
        self,
        script: str,
        args: Sequence[str] = (),
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        profiles = {path.name: path.read_bytes() for path in self.home.iterdir() if path.is_file()}
        environment = {
            "HOME": str(self.home),
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "LC_ALL": "C",
            "SANDBOX_ROOT": str(self.root),
            "UV_LOG": str(self.log),
            "INSTALLER_LOG": str(self.installer_log),
            "CHECK_COUNT": str(self.log.parent / "checks"),
            "PROFILE_MARKER": str(self.profile_marker),
            "UV_NO_ENV_FILE": "1",
        }
        if extra_env is not None:
            environment.update(extra_env)

        completed = subprocess.run(
            ["/bin/bash", str(self.root / script), *args],
            cwd=self.home,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )

        assert {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()} == before
        assert {path.name: path.read_bytes() for path in self.home.iterdir() if path.is_file()} == profiles
        assert not self.profile_marker.exists()
        assert not self.installer_log.exists(), "installer attempted to run"
        return completed

    def calls(self) -> list[tuple[str, ...]]:
        if not self.log.exists():
            return []
        return [tuple(record.decode().split("\0")[:-1]) for record in self.log.read_bytes().split(b"\n") if record]


@pytest.fixture
def sandbox(tmp_path: Path) -> QualitySandbox:
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (root / "scripts" / "git-hooks").mkdir(parents=True)
    for name in WRAPPERS:
        target = root / name
        shutil.copy2(REPOSITORY / name, target)
        target.chmod(0o755)
    hook = root / "scripts" / "git-hooks" / "pre-push"
    shutil.copy2(REPOSITORY / "scripts" / "git-hooks" / "pre-push", hook)
    hook.chmod(0o755)

    (root / "prdiffer").mkdir()
    (root / "prdiffer" / "sample.py").write_bytes(b'def sample():\n    """A docstring."""\n    return 1  \n')
    (root / "tests").mkdir()
    (root / "tests" / "test_sample.py").write_bytes(b"def test_sample():\n    assert True\n")
    (root / "pyproject.toml").write_bytes(b'[project]\nname = "sandbox"\nversion = "0.1.0"\n[tool.ruff]\nline-length = 100\n[tool.ty]\n')
    (root / "uv.lock").write_bytes(b'version = 1\nrequires-python = ">=3.14"\n')
    (root / ".env").write_bytes(b'GITHUB_TOKEN="$(touch "$PROFILE_MARKER")"\n')
    (root / ".venv").mkdir()
    (root / ".venv" / "pyvenv.cfg").write_bytes(b"home = /isolated/python\n")
    (home / ".bashrc").write_text('touch "$PROFILE_MARKER"\n', encoding="utf-8")
    (home / ".zshrc").write_text('touch "$PROFILE_MARKER"\n', encoding="utf-8")

    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        """#!/bin/bash
printf '%s\\0' "$PWD" "$@" >> "$UV_LOG"
printf '\\n' >> "$UV_LOG"
if [[ -n "${FAKE_MISSING_TOOL:-}" && " $* " == *" ${FAKE_MISSING_TOOL} "* ]]; then
    exit 127
fi
if [[ -n "${FAKE_FAIL_MATCH:-}" && " $* " == *" ${FAKE_FAIL_MATCH} "* ]]; then
    exit 37
fi
if [[ "$*" == *"ruff check ."* ]]; then
    count=0
    [[ ! -f "$CHECK_COUNT" ]] || count=$(<"$CHECK_COUNT")
    count=$((count + 1))
    printf '%s' "$count" > "$CHECK_COUNT"
    if [[ "${FAKE_FAIL_CHECK_NUMBER:-0}" -eq "$count" ]]; then
        exit 37
    fi
fi
printf 'fake-tool 1.0\\n'
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    (bin_dir / "git").write_text('#!/bin/bash\n[[ "$*" == "rev-parse --show-toplevel" ]] || exit 2\nprintf "%s\\n" "$SANDBOX_ROOT"\n', encoding="utf-8")
    (bin_dir / "git").chmod(0o755)
    for name in ("curl", "wget", "pip"):
        stub = bin_dir / name
        stub.write_text('#!/bin/bash\nprintf "%s\\n" "$0 $*" >> "$INSTALLER_LOG"\nexit 77\n', encoding="utf-8")
        stub.chmod(0o755)
    return QualitySandbox(root, home, bin_dir, tmp_path / "uv.log", tmp_path / "installers.log", tmp_path / "profile-ran")


def expected_call(sandbox: QualitySandbox, *command: str) -> tuple[str, ...]:
    return (str(sandbox.root), "run", "--project", str(sandbox.root), "--frozen", "--no-sync", "--no-python-downloads", *command)


def commands(sandbox: QualitySandbox) -> list[tuple[str, ...]]:
    calls = [call for call in sandbox.calls() if call[1:] != ("--version",)]
    for call in calls:
        assert call[0] == str(sandbox.root)
        assert call[:7] == expected_call(sandbox)[:7] or call[:8] == (*expected_call(sandbox), "--no-env-file")
    return [call[8:] if call[7] == "--no-env-file" else call[7:] for call in calls]


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        pytest.param("start-lint.sh", ("ruff", "check", ".", "--output-format=full"), id="lint"),
        pytest.param("start-type-check.sh", ("ty", "check"), id="type"),
        pytest.param("start-unittest.sh", ("pytest", "tests", "-v"), id="tests"),
    ],
)
def test_default_quality_check_is_immutable(sandbox: QualitySandbox, script: str, expected: tuple[str, ...]) -> None:
    completed = sandbox.invoke(script)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert expected in commands(sandbox)
    assert all("install" not in call and "sync" not in call and "add" not in call and "--upgrade" not in call for call in commands(sandbox))


@pytest.mark.parametrize("script", WRAPPERS)
def test_missing_uv_fails_without_install_or_profile(sandbox: QualitySandbox, script: str) -> None:
    (sandbox.bin_dir / "uv").unlink()

    completed = sandbox.invoke(script)

    assert completed.returncode != 0
    assert sandbox.calls() == []


@pytest.mark.parametrize(
    ("script", "tool"),
    [("start-lint.sh", "ruff"), ("start-type-check.sh", "ty"), ("start-unittest.sh", "pytest")],
)
def test_missing_quality_tool_fails_without_install(sandbox: QualitySandbox, script: str, tool: str) -> None:
    completed = sandbox.invoke(script, extra_env={"FAKE_MISSING_TOOL": tool})

    assert completed.returncode != 0
    assert all("install" not in call and "add" not in call for call in commands(sandbox))


@pytest.mark.parametrize(
    ("script", "failure"),
    [("start-lint.sh", "ruff check ."), ("start-type-check.sh", "ty check"), ("start-unittest.sh", "pytest tests")],
)
def test_quality_failure_blocks_success(sandbox: QualitySandbox, script: str, failure: str) -> None:
    completed = sandbox.invoke(script, extra_env={"FAKE_FAIL_MATCH": failure})

    assert completed.returncode != 0
    assert any(failure in " ".join(call) for call in commands(sandbox))


def test_all_reports_final_lint_failure(sandbox: QualitySandbox) -> None:
    (sandbox.root / "prdiffer" / "sample.py").write_bytes(b'def sample():\n    """A docstring."""\n    return 1\n')
    completed = sandbox.invoke("start-lint.sh", ("--all",), extra_env={"CHECK_COUNT": str(sandbox.log.parent / "checks"), "FAKE_FAIL_CHECK_NUMBER": "3"})

    assert commands(sandbox).count(("ruff", "check", ".", "--output-format=full")) >= 2
    assert completed.returncode != 0


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param(("--file", "tests/test_sample.py"), ("pytest", "tests/test_sample.py", "-v"), id="file"),
        pytest.param(("--pattern", "sample and not slow"), ("pytest", "tests", "-v", "-k", "sample and not slow"), id="pattern"),
    ],
)
def test_targeted_tests_preserve_argument_boundaries(sandbox: QualitySandbox, args: tuple[str, ...], expected: tuple[str, ...]) -> None:
    completed = sandbox.invoke("start-unittest.sh", args)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert expected in commands(sandbox)


def test_missing_tests_fails_without_scaffolding(sandbox: QualitySandbox) -> None:
    shutil.rmtree(sandbox.root / "tests")

    completed = sandbox.invoke("start-unittest.sh")

    assert completed.returncode != 0
    assert not (sandbox.root / "tests").exists()


@pytest.mark.parametrize("failure", ("", "ty check", "ruff check ."), ids=("success", "type-failure", "lint-failure"))
def test_pre_push_is_read_only_and_blocks_failures(sandbox: QualitySandbox, failure: str) -> None:
    completed = sandbox.invoke("scripts/git-hooks/pre-push", extra_env={"FAKE_FAIL_MATCH": failure, "CHECK_COUNT": str(sandbox.log.parent / "checks")})
    invoked = commands(sandbox)

    assert completed.returncode == (0 if not failure else 1)
    assert ("ty", "check") in invoked
    if failure != "ty check":
        assert ("ruff", "check", ".", "--output-format=full") in invoked
    else:
        assert not any("ruff" in call for call in invoked)
    assert all("--fix" not in call and "format" not in call for call in invoked)
