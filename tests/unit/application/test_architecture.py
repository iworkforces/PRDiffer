"""Both architecture gate consumers enforce the same AST engine and rules."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "analyze_dependencies.py"


def _load_engine() -> ModuleType:
    name = "analyze_dependencies_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


engine = _load_engine()


@pytest.fixture
def package(tmp_path: Path) -> Path:
    root = tmp_path / "prdiffer"
    for directory in (root, root / "domain", root / "application", root / "infrastructure"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    (root / "infrastructure" / "settings.py").write_text("X = 1\n", encoding="utf-8")
    return root


def _write(package: Path, relative: str, source: str) -> Path:
    path = package / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _run_cli(package: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), "--path", str(package)], capture_output=True, text=True, timeout=60)


@pytest.mark.unit
def test_real_tree_is_clean() -> None:
    result = engine.scan_package(ROOT / "prdiffer")
    assert result.violations == [] and result.parse_failures == [], "Architecture failures:\n" + "\n".join(engine.format_failures(result))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("relative", "source", "rule", "target", "lineno"),
    [
        (
            "application/mod.py",
            "def load():\n    from prdiffer.infrastructure.settings import X\n",
            "application->infrastructure",
            "prdiffer.infrastructure.settings",
            2,
        ),
        ("application/mod.py", "from ..infrastructure import settings\n", "application->infrastructure", "prdiffer.infrastructure", 1),
        ("application/__init__.py", "from ..infrastructure import settings\n", "application->infrastructure", "prdiffer.infrastructure", 1),
        ("application/mod.py", "from .. import infrastructure\n", "application->infrastructure", "prdiffer.infrastructure", 1),
        (
            "application/mod.py",
            "def load():\n    import prdiffer.infrastructure.settings\n",
            "application->infrastructure",
            "prdiffer.infrastructure.settings",
            2,
        ),
        ("application/mod.py", "from prdiffer import infrastructure\n", "application->infrastructure", "prdiffer.infrastructure", 1),
        ("domain/mod.py", "from prdiffer.application import tool_registry\n", "domain->application", "prdiffer.application", 1),
        ("domain/mod.py", "from prdiffer.infrastructure import settings\n", "domain->infrastructure", "prdiffer.infrastructure", 1),
        ("application/sub/factory.py", "import prdiffer.infrastructure.settings\n", "application->infrastructure", "prdiffer.infrastructure.settings", 1),
        ("domain/factory.py", "import prdiffer.infrastructure.settings\n", "domain->infrastructure", "prdiffer.infrastructure.settings", 1),
        ("application/mod.py", "from ..infrastructure.settings import X as value\n", "application->infrastructure", "prdiffer.infrastructure.settings.X", 1),
        ("application/mod.py", "from prdiffer.infrastructure import *\n", "application->infrastructure", "prdiffer.infrastructure", 1),
        (
            "application/mod.py",
            "if False:\n    import prdiffer.infrastructure.settings as settings\n",
            "application->infrastructure",
            "prdiffer.infrastructure.settings",
            2,
        ),
        (
            "application/mod.py",
            "try:\n    import prdiffer.infrastructure.settings\nexcept ImportError:\n    raise\n",
            "application->infrastructure",
            "prdiffer.infrastructure.settings",
            2,
        ),
        ("application/mod.py", "class Handler:\n    from prdiffer import infrastructure\n", "application->infrastructure", "prdiffer.infrastructure", 2),
        (
            "application/mod.py",
            "class Handler:\n    def load(self):\n        from prdiffer import infrastructure\n",
            "application->infrastructure",
            "prdiffer.infrastructure",
            3,
        ),
        ("application/test/mod.py", "import prdiffer.infrastructure\n", "application->infrastructure", "prdiffer.infrastructure", 1),
    ],
)
def test_both_consumers_reject_forbidden_imports(package: Path, relative: str, source: str, rule: str, target: str, lineno: int) -> None:
    _write(package, relative, source)
    module = "prdiffer." + relative.removesuffix(".py").replace("/", ".").removesuffix(".__init__")

    result = engine.scan_package(package)
    cli = _run_cli(package)

    assert result.parse_failures == []
    assert not result.is_clean
    assert (module, target, rule, lineno) in [(v.module, v.target, v.rule, v.lineno) for v in result.violations]
    messages = engine.format_failures(result)
    assert any(f"prdiffer/{relative}:{lineno}: {rule}: {module}" in message for message in messages)
    assert cli.returncode == 1, cli.stdout + cli.stderr
    assert f"prdiffer/{relative}:{lineno}: {rule}" in cli.stdout


@pytest.mark.unit
@pytest.mark.parametrize("source", ["import prdiffer.infrastructure.settings\n", "def load():\n    from ..infrastructure import settings\n"])
def test_exact_composition_root_is_exempt(package: Path, source: str) -> None:
    _write(package, "application/factory.py", source)

    result = engine.scan_package(package)
    cli = _run_cli(package)

    assert result.is_clean, engine.format_failures(result)
    assert result.violations == [] and result.parse_failures == []
    assert "prdiffer.infrastructure.settings" in result.dependencies["prdiffer.application.factory"]
    assert cli.returncode == 0, cli.stdout + cli.stderr


@pytest.mark.unit
@pytest.mark.parametrize(
    ("relative", "source", "rule", "lineno"),
    [
        ("application/mod.py", b"from ... import infrastructure\n", "relative-import", 1),
        ("__init__.py", b"from .. import infrastructure\n", "relative-import", 1),
        ("application/mod.py", b"\ndef broken(:\n", "parse-failure", 2),
        ("application/mod.py", b"# coding: utf-8\nvalue = '\xff'\n", "parse-failure", 2),
        ("application/mod.py", b"# coding: unknown-encoding\n", "parse-failure", 1),
    ],
)
def test_both_consumers_reject_unscannable_source(package: Path, relative: str, source: bytes, rule: str, lineno: int) -> None:
    (package / relative).write_bytes(source)

    result = engine.scan_package(package)
    cli = _run_cli(package)

    assert not result.is_clean
    assert result.violations == []
    assert len(result.parse_failures) == 1
    failure = result.parse_failures[0]
    assert (failure.path, failure.lineno, failure.rule) == (f"prdiffer/{relative}", lineno, rule)
    assert failure.error
    assert engine.format_failures(result)[0] in cli.stdout
    assert cli.returncode == 1, cli.stdout + cli.stderr


@pytest.mark.unit
def test_both_consumers_accept_clean_tree(package: Path) -> None:
    _write(package, "domain/mod.py", "from . import entity\nfrom prdiffer.domain import entity\n")
    _write(package, "domain/entity.py", "X = 1\n")
    _write(package, "application/mod.py", "from ..domain import entity\nfrom . import sibling\nfrom prdiffer.application import sibling\n")
    _write(package, "application/sibling.py", "from prdiffer import infrastructure_extra\n")
    _write(package, "application_extra/mod.py", "import prdiffer.infrastructure\n")
    _write(package, "infrastructure/mod.py", "from prdiffer import domain, application, infrastructure\n")
    _write(package, "server.py", "import prdiffer.infrastructure\n")
    _write(package, "application/__pycache__/broken.py", "def broken(:\n")
    (package / "domain" / "encoded.py").write_bytes(b"# coding: latin-1\nlabel = '\xe9'\nfrom . import entity\n")

    result = engine.scan_package(package)
    cli = _run_cli(package)

    assert result.is_clean, engine.format_failures(result)
    assert result.violations == [] and result.parse_failures == []
    assert engine.format_failures(result) == []
    assert result.dependencies["prdiffer.domain.mod"] == {"prdiffer.domain", "prdiffer.domain.entity"}
    assert result.dependencies["prdiffer.application.mod"] == {
        "prdiffer.domain",
        "prdiffer.domain.entity",
        "prdiffer.application",
        "prdiffer.application.sibling",
    }
    assert "prdiffer" in result.dependencies
    assert cli.returncode == 0, cli.stdout + cli.stderr


@pytest.mark.unit
def test_missing_package_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    result = engine.scan_package(missing)
    cli = _run_cli(missing)

    assert not result.is_clean and len(result.parse_failures) == 1
    assert cli.returncode == 1
    assert str(missing) in cli.stderr


@pytest.mark.unit
def test_loading_engine_has_no_cli_side_effects(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delitem(sys.modules, "analyze_dependencies_under_test")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--invalid-cli-option"])

    loaded = _load_engine()

    assert _load_engine() is loaded
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
