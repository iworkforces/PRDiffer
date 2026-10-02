"""Ordered strict FileProcessor assembly tests (immutable git tree path)."""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.infrastructure.github.file_processor import FileProcessor

HEAD = "c" * 40
BASE = "b" * 40


class AcceptAllMatcher:
    def is_valid_file(self, filename: str) -> bool:
        return True


def _file(
    name: str,
    status: str,
    *,
    previous: str | None = None,
    patch: str | None = "@@",
    additions: int = 1,
    deletions: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        filename=name,
        status=status,
        previous_filename=previous,
        patch=patch,
        additions=additions,
        deletions=deletions,
    )


class TreeRepo:
    """Tree/blob fake: ``contents[ref][path] = text`` (regular 100644 blobs)."""

    def __init__(self, contents: dict[str, dict[str, str]]) -> None:
        self.full_name = "o/r"
        self._trees: dict[str, list[SimpleNamespace]] = {}
        self._blobs: dict[str, str] = {}
        self.tree_calls: list[str] = []
        self.blob_calls: list[str] = []
        for ref, files in contents.items():
            items: list[SimpleNamespace] = []
            for path, text in files.items():
                oid = hashlib.sha1(f"{ref}:{path}:{text}".encode()).hexdigest()
                self._blobs[oid] = text
                items.append(SimpleNamespace(path=path, mode="100644", type="blob", sha=oid))
            self._trees[ref] = items

    def get_git_tree(self, sha: str, recursive: bool = False) -> SimpleNamespace:
        self.tree_calls.append(sha)
        return SimpleNamespace(truncated=False, tree=list(self._trees.get(sha, [])))

    def get_git_blob(self, sha: str) -> SimpleNamespace:
        self.blob_calls.append(sha)
        return SimpleNamespace(encoding="base64", content=base64.b64encode(self._blobs[sha].encode()).decode())


@pytest.fixture
def processor() -> FileProcessor:
    return FileProcessor(pattern_matcher=AcceptAllMatcher(), max_files_allowed=50)


@pytest.mark.unit
class TestOrderedAssembly:
    def test_mixed_statuses_preserve_order(self, processor: FileProcessor) -> None:
        files = [
            _file("mod.py", "modified"),
            _file("del.py", "removed", deletions=1),
            _file("new.py", "renamed", previous="old.py"),
            _file("add.py", "added"),
        ]
        repo = TreeRepo(
            {
                BASE: {"mod.py": "base:mod.py\n", "del.py": "base:del.py\n", "old.py": "same\n"},
                HEAD: {"mod.py": "head:mod.py\n", "new.py": "same\n", "add.py": "head:add.py\n"},
            }
        )

        result = processor.process_files_to_patches(files, repo, HEAD, BASE)

        assert [p.filename for p in result] == ["mod.py", "del.py", "new.py", "add.py"]
        assert [p.edit_type for p in result] == [
            EDIT_TYPE.MODIFIED,
            EDIT_TYPE.DELETED,
            EDIT_TYPE.RENAMED,
            EDIT_TYPE.ADDED,
        ]
        assert result[0].base_file == "base:mod.py\n"
        assert result[0].head_file == "head:mod.py\n"
        assert result[1].base_file == "base:del.py\n"
        assert result[1].head_file == ""
        assert result[2].old_filename == "old.py"
        assert result[2].base_file == result[2].head_file == "same\n"
        assert result[3].base_file == ""
        assert result[3].head_file == "head:add.py\n"
        assert repo.tree_calls == [BASE, HEAD]

    def test_unknown_status_rejected(self, processor: FileProcessor) -> None:
        files = [_file("x.py", "copied")]
        repo = TreeRepo({})
        with pytest.raises(FullDiffIncompleteError) as exc:
            processor.process_files_to_patches(files, repo, HEAD, BASE)
        assert exc.value.reason is FullDiffIncompleteReason.UNSUPPORTED_FILE_STATUS
        assert repo.tree_calls == []


def test_missing_tree_api_fails_closed() -> None:
    proc = FileProcessor(pattern_matcher=MagicMock(is_valid_file=lambda p: True))
    files = [SimpleNamespace(filename="a.py", status="modified", patch="", additions=1, deletions=0)]
    with pytest.raises(FullDiffIncompleteError) as ei:
        proc.process_files_to_patches(files, SimpleNamespace(full_name="o/r"), HEAD, BASE)
    assert ei.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED


def test_rename_without_previous_filename_fails_before_content() -> None:
    proc = FileProcessor(pattern_matcher=MagicMock(is_valid_file=lambda p: True))
    repo = TreeRepo({})
    files = [SimpleNamespace(filename="new.py", status="renamed", previous_filename=None, patch="")]
    with pytest.raises(FullDiffIncompleteError) as ei:
        proc.process_files_to_patches(files, repo, HEAD, BASE)
    assert ei.value.reason is FullDiffIncompleteReason.UNSUPPORTED_FILE_STATUS
    assert repo.tree_calls == []


def test_tree_path_assembles_modes_and_gitlink() -> None:
    oid_blob = "1" * 40
    oid_link = "2" * 40

    def get_git_tree(sha, recursive=False):
        if sha == HEAD:
            items = [
                SimpleNamespace(path="sub", mode="160000", type="commit", sha=oid_link),
                SimpleNamespace(path="a.py", mode="100644", type="blob", sha=oid_blob),
            ]
        else:
            items = [
                SimpleNamespace(path="a.py", mode="100644", type="blob", sha=oid_blob),
            ]
        return SimpleNamespace(truncated=False, tree=items)

    def get_git_blob(sha):
        assert sha == oid_blob
        return SimpleNamespace(encoding="utf-8", content="hello\n", decoded_content=b"hello\n")

    repo = SimpleNamespace(full_name="o/r", get_git_tree=get_git_tree, get_git_blob=get_git_blob)
    proc = FileProcessor(pattern_matcher=MagicMock(is_valid_file=lambda p: True))
    files = [
        SimpleNamespace(filename="a.py", status="modified", patch="", additions=1, deletions=0),
        SimpleNamespace(filename="sub", status="added", patch="", additions=1, deletions=0),
    ]
    patches = proc.process_files_to_patches(files, repo, HEAD, BASE)
    assert len(patches) == 2
    assert patches[0].edit_type is EDIT_TYPE.MODIFIED
    assert patches[0].base_file == "hello\n"
    assert patches[0].head_file == "hello\n"
    assert patches[0].old_mode == "100644"
    assert patches[0].new_mode == "100644"
    assert patches[1].edit_type is EDIT_TYPE.ADDED
    assert patches[1].head_file == f"Subproject commit {oid_link}\n"
    assert patches[1].new_mode == "160000"


def test_max_file_size_bytes_is_enforced_on_blobs() -> None:
    repo = TreeRepo({BASE: {"a.py": "x" * 10}, HEAD: {"a.py": "y" * 11}})
    proc = FileProcessor(pattern_matcher=AcceptAllMatcher(), max_file_size_bytes=10)
    with pytest.raises(FullDiffIncompleteError) as ei:
        proc.process_files_to_patches([_file("a.py", "modified")], repo, HEAD, BASE)
    assert ei.value.reason is FullDiffIncompleteReason.FILE_SIZE_LIMIT
