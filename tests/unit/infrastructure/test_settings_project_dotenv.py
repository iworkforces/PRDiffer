"""Project-root .env loading is independent of process cwd."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prdiffer.infrastructure.settings import load_project_dotenv, project_root


@pytest.fixture
def dotenv_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr("prdiffer.infrastructure.settings.project_root", lambda: tmp_path)
    monkeypatch.setenv("GITHUB_IGNORE_PATTERNS", "fixture-initial.lock")
    return tmp_path


@pytest.mark.unit
def test_project_root_points_at_repo_with_settings_toml() -> None:
    root = project_root()
    assert (root / "settings.toml").is_file()
    assert (root / "prdiffer").is_dir()


@pytest.mark.unit
def test_load_project_dotenv_sets_github_ignore_patterns(monkeypatch: pytest.MonkeyPatch, dotenv_root: Path) -> None:
    """Project-root .env is loaded even when GITHUB_IGNORE_PATTERNS was unset."""
    monkeypatch.delenv("GITHUB_IGNORE_PATTERNS", raising=False)
    (dotenv_root / ".env").write_text("GITHUB_IGNORE_PATTERNS=fixture.lock,fixture/\n", encoding="utf-8")
    loaded = load_project_dotenv(override=False)
    assert loaded == dotenv_root / ".env"
    assert os.environ["GITHUB_IGNORE_PATTERNS"] == "fixture.lock,fixture/"


def test_project_dotenv_preserves_existing_environment(monkeypatch: pytest.MonkeyPatch, dotenv_root: Path) -> None:
    (dotenv_root / ".env").write_text("GITHUB_IGNORE_PATTERNS=dotenv.lock\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_IGNORE_PATTERNS", "ambient.lock")
    load_project_dotenv()
    assert os.environ["GITHUB_IGNORE_PATTERNS"] == "ambient.lock"


def test_project_dotenv_returns_none_when_missing(dotenv_root: Path) -> None:
    assert load_project_dotenv() is None
