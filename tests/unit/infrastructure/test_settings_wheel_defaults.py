"""Offline artifact/runtime checks against the canonical root settings."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tomllib
import zipfile
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[3]
CANONICAL = ROOT / "settings.toml"
PATHS = [
    "src/main.py",
    "docs/readme.md",
    "uv.lock",
    "package-lock.json",
    "images/logo.png",
    "node_modules/index.js",
    "nested/node_modules/index.ts",
    ".github/workflows/ci.yml",
    "src/unknown.xyz",
]
PROBE = """
import json, sys
from dataclasses import asdict
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import prdiffer.infrastructure.settings as module
from prdiffer.infrastructure.utils.pattern_matcher import PatternMatcher
request = json.load(sys.stdin)
if request.get("checkout_root"):
    module.project_root = lambda: Path(request["checkout_root"])
service = module.SettingsService(settings_files=request.get("settings_files"))
github = service.get_github_config()
matcher = PatternMatcher(list(github.ignore_patterns), list(github.valid_extensions))
print(json.dumps({
    "module": str(Path(module.__file__).resolve()),
    "values": {key: service.get(key) for key in request["keys"]},
    "github": asdict(github), "gitlab": asdict(service.get_gitlab_config()),
    "cache": service.get_cache_settings(), "app": service.get_app_settings(),
    "filter": {path: matcher.is_valid_file(path) for path in request["paths"]},
    "cached": service.get_github_config() is github,
}))
"""

type TomlValue = str | int | float | bool | list[TomlValue] | dict[str, TomlValue]


def canonical_values() -> dict[str, TomlValue]:
    def flatten(table: dict[str, TomlValue], prefix: str = "") -> dict[str, TomlValue]:
        result: dict[str, TomlValue] = {}
        for key, value in table.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                result.update(flatten(value, name))
            else:
                result[name] = value
        return result

    return flatten(tomllib.loads(CANONICAL.read_text(encoding="utf-8"))["default"])


@dataclass(frozen=True, slots=True)
class Layout:
    package_root: Path
    dotenv_root: Path
    checkout: bool = False

    def probe(self, cwd: Path, *, files: list[str] | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {"keys": list(canonical_values()), "paths": PATHS, "settings_files": files}
        if self.checkout:
            request["checkout_root"] = str(self.dotenv_root)
        environment = {"HOME": str(cwd), "PATH": os.defpath}
        environment.update(env or {})
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", PROBE, str(self.package_root), sysconfig.get_path("purelib")],
            cwd=cwd,
            env=environment,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        snapshot = json.loads(result.stdout)
        assert Path(snapshot.pop("module")).is_relative_to(self.package_root)
        return snapshot


@pytest.fixture(scope="session", params=["checkout", "direct", "sdist"])
def layout(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Layout:
    temporary = tmp_path_factory.mktemp(f"settings-{request.param}")
    if request.param == "checkout":
        shutil.copy2(CANONICAL, temporary / "settings.toml")
        return Layout(ROOT, temporary, checkout=True)

    output = temporary / "artifacts"
    output.mkdir()
    command = ["uv", "build", "--offline", "--no-build-isolation", "--out-dir", str(output)]
    if request.param == "direct":
        command.append("--wheel")
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True, timeout=60)
    (wheel,) = output.glob("*.whl")
    installed = temporary / "installed"
    installed.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        assert not any(Path(name).name in {".env", ".secrets.toml"} for name in archive.namelist())
        archive.extractall(installed)
    if request.param == "sdist":
        (sdist,) = output.glob("*.tar.gz")
        with tarfile.open(sdist) as archive:
            assert not any(Path(name).name in {".env", ".secrets.toml"} for name in archive.getnames())
            (member,) = [entry for entry in archive.getmembers() if entry.name.endswith("/settings.toml")]
            source = archive.extractfile(member)
            assert source is not None
            with source:
                assert source.read() == CANONICAL.read_bytes()
    return Layout(installed, installed)


def test_every_canonical_default_resolves_when_running_layout(layout: Layout, tmp_path: Path) -> None:
    # Given an empty cwd and an isolated import path/environment; When resolving defaults.
    snapshot = layout.probe(tmp_path)
    # Then every canonical leaf and filtering contract survives installation.
    assert snapshot["values"] == canonical_values()
    assert snapshot["filter"] == dict(zip(PATHS, [True, True, False, False, False, False, False, False, False], strict=True))
    assert snapshot["cache"]["ttl"] == canonical_values()["cache.ttl"]
    assert snapshot["cached"] is True


def test_layout_effective_settings_match_checkout(layout: Layout, tmp_path: Path) -> None:
    # Given a checkout root with canonical settings but no real dotenv/secrets.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(CANONICAL, checkout / "settings.toml")
    # When running the checkout and target layout outside either root.
    expected = Layout(ROOT, checkout, checkout=True).probe(tmp_path)
    # Then public typed configs, caches, all settings and filtering agree.
    assert layout.probe(tmp_path) == expected


def test_packaged_source_cannot_drift_from_canonical(layout: Layout) -> None:
    if layout.checkout:
        return  # Checkout has only the canonical file; no generated resource is maintained there.
    assert (layout.package_root / "prdiffer" / "settings.toml").read_bytes() == CANONICAL.read_bytes()


def test_cwd_settings_replace_defaults_when_present(layout: Layout, tmp_path: Path) -> None:
    # Given partial cwd settings distinct from canonical values.
    (tmp_path / "settings.toml").write_text("[default]\ncache.ttl=731\ngithub.ignore_patterns=['cwd.lock']\n", encoding="utf-8")
    snapshot = layout.probe(tmp_path)
    # Then omitted values use existing code fallbacks, not packaged TOML.
    assert snapshot["cache"]["ttl"] == 731
    assert snapshot["github"]["ignore_patterns"] == ["cwd.lock"]
    assert snapshot["github"]["valid_extensions"] == []
    assert snapshot["values"]["mcp.port"] is None
    assert snapshot["filter"]["images/logo.png"] is True


def test_explicit_files_win_over_cwd_and_defaults(layout: Layout, tmp_path: Path) -> None:
    (tmp_path / "settings.toml").write_text("[default]\ncache.ttl=731\n", encoding="utf-8")
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[default]\ncache.ttl=947\ngithub.ignore_patterns=['explicit.lock']\n", encoding="utf-8")
    snapshot = layout.probe(tmp_path, files=[str(explicit)])
    assert snapshot["cache"]["ttl"] == 947
    assert snapshot["github"]["ignore_patterns"] == ["explicit.lock"]
    assert snapshot["values"]["mcp.port"] is None


def test_named_environment_overrides_win_over_files(layout: Layout, tmp_path: Path) -> None:
    (tmp_path / "settings.toml").write_text(
        "[default]\ngithub.ignore_patterns=['file.lock']\ngitlab.allowed_hosts=['file.example']\napp.max_files_allowed=61\ndiff.max_total_chars=654321\n",
        encoding="utf-8",
    )
    snapshot = layout.probe(
        tmp_path,
        env={
            "GITHUB_IGNORE_PATTERNS": "env.lock, env/",
            "GITLAB_ALLOWED_HOSTS": "env.example, second.example",
            "MAX_FILES_ALLOWED": "83",
            "MAX_TOTAL_CHARS": "765432",
        },
    )
    assert snapshot["github"]["ignore_patterns"] == ["env.lock", "env/"]
    assert snapshot["gitlab"]["allowed_hosts"] == ["env.example", "second.example"]
    assert snapshot["github"]["max_files_allowed"] == snapshot["gitlab"]["max_files_allowed"] == snapshot["app"]["max_files_allowed"] == 83
    assert snapshot["github"]["max_total_chars"] == snapshot["gitlab"]["max_total_chars"] == 765432


@pytest.mark.parametrize("ambient", [False, True])
def test_layout_constructor_loads_dotenv_without_overriding_environment(layout: Layout, tmp_path: Path, ambient: bool) -> None:
    # Given a temporary project/install root with a deterministic dotenv fixture.
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    if layout.checkout:
        shutil.copy2(CANONICAL, isolated / "settings.toml")
        target = Layout(ROOT, isolated, checkout=True)
    else:
        shutil.copytree(layout.package_root / "prdiffer", isolated / "prdiffer")
        target = Layout(isolated, isolated)
    (isolated / ".env").write_text("GITHUB_IGNORE_PATTERNS=dotenv.lock\n", encoding="utf-8")
    # When constructing from another cwd, possibly with an existing environment value.
    snapshot = target.probe(tmp_path, env={"GITHUB_IGNORE_PATTERNS": "ambient.lock"} if ambient else {})
    # Then dotenv applies but never replaces existing process configuration.
    assert snapshot["github"]["ignore_patterns"] == ["ambient.lock" if ambient else "dotenv.lock"]
