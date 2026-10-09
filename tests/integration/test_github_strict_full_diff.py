"""No-network GitHub strict full-diff integration matrix.

Wires real session reader, use case, FileProcessor, DiffGenerator, and service
around a stateful fake PyGithub surface (no network).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff_cache import github_full_diff_v3_identity
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason, GitHubAPIError
from prdiffer.domain.usecases.pr_diff_usecases import GetPRDiffUseCase
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.github.file_processor import FileProcessor
from prdiffer.infrastructure.github.pr_diff_session import GitHubSessionPRDiffReader
from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService
from prdiffer.infrastructure.utils.diff_utils import DiffUtils

BASE_TIP = "a" * 40
MERGE_BASE = "b" * 40
HEAD = "c" * 40
HEAD2 = "d" * 40
BLOB_A = "1" * 40
BLOB_B = "2" * 40
BLOB_C = "3" * 40
BLOB_OLD = "4" * 40
BLOB_NEW = "5" * 40
BLOB_LINK = "6" * 40
GITLINK = "7" * 40
BLOB_EMPTY = "8" * 40


class MemoryCache:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], Any] = {}
        self.sets = 0
        self.gets = 0

    async def get(self, key: str, token: str) -> Any:
        self.gets += 1
        return self.store.get((key, token))

    async def set(self, key: str, token: str, value: Any) -> None:
        self.sets += 1
        self.store[(key, token)] = value


class FakeFile:
    def __init__(
        self,
        filename: str,
        status: str,
        *,
        previous_filename: str | None = None,
        additions: int = 1,
        deletions: int = 0,
        patch: str = "",
    ) -> None:
        self.filename = filename
        self.status = status
        self.previous_filename = previous_filename
        self.additions = additions
        self.deletions = deletions
        self.patch = patch


class FakeRepo:
    def __init__(
        self,
        *,
        trees: dict[str, list[Any]],
        blobs: dict[str, bytes],
        pr: Any,
        full_name: str = "acme/demo",
        compare_error: BaseException | None = None,
        revalidate_pr: Any | None = None,
    ) -> None:
        self.full_name = full_name
        self._trees = trees
        self._blobs = blobs
        self._pr = pr
        self._revalidate_pr = revalidate_pr or pr
        self.compare_error = compare_error
        self.compare_calls: list[tuple[str, str]] = []
        self.tree_calls: list[str] = []
        self.blob_calls: list[str] = []
        self.get_pull_calls = 0

    def compare(self, base: str, head: str) -> Any:
        self.compare_calls.append((base, head))
        if self.compare_error is not None:
            raise self.compare_error
        return SimpleNamespace(merge_base_commit=SimpleNamespace(sha=MERGE_BASE))

    def get_git_tree(self, sha: str, recursive: bool = False) -> Any:
        self.tree_calls.append(sha)
        items = self._trees.get(sha)
        if items is None:
            raise RuntimeError(f"missing tree {sha}")
        return SimpleNamespace(truncated=False, tree=items)

    def get_git_blob(self, sha: str) -> Any:
        self.blob_calls.append(sha)
        data = self._blobs.get(sha)
        if data is None:
            raise RuntimeError(f"missing blob {sha}")
        return SimpleNamespace(encoding="utf-8", content=data.decode("utf-8"), decoded_content=data)

    def get_pull(self, number: int) -> Any:
        self.get_pull_calls += 1
        return self._revalidate_pr


class FakeAPI:
    def __init__(self, repo: FakeRepo, pr: Any) -> None:
        self._github_client = object()
        self._repo = repo
        self._pr = pr
        self.repo_lookups = 0
        self.pr_lookups = 0

    def _get_pygithub_repository(self, full_name: str) -> FakeRepo:
        self.repo_lookups += 1
        assert full_name == self._repo.full_name
        return self._repo

    def _get_pygithub_pull_request(self, repository: FakeRepo, pr_number: int) -> Any:
        self.pr_lookups += 1
        return self._pr


def _tree_item(path: str, mode: str, otype: str, sha: str) -> SimpleNamespace:
    return SimpleNamespace(path=path, mode=mode, type=otype, sha=sha)


def _build_stack(
    *,
    files: list[FakeFile],
    trees: dict[str, list[Any]],
    blobs: dict[str, bytes],
    changed_files: int | None = None,
    revalidate_pr: Any | None = None,
    compare_error: BaseException | None = None,
) -> tuple[GitHubSessionPRDiffReader, MemoryCache, FakeAPI, FakeRepo]:
    class FakePR:
        def __init__(self) -> None:
            self.base = SimpleNamespace(sha=BASE_TIP)
            self.head = SimpleNamespace(sha=HEAD)
            self.changed_files = len(files) if changed_files is None else changed_files
            self._files = files
            self.get_files_calls = 0

        def get_files(self) -> list[FakeFile]:
            self.get_files_calls += 1
            return list(self._files)

    pr = FakePR()
    repo = FakeRepo(trees=trees, blobs=blobs, pr=pr, revalidate_pr=revalidate_pr, compare_error=compare_error)
    api = FakeAPI(repo, pr)

    pattern = SimpleNamespace(is_valid_file=lambda path: True)
    processor = FileProcessor(
        pattern_matcher=pattern,  # type: ignore[arg-type]
        max_files_allowed=50,
    )
    generator = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)

    service = GitHubPRDiffService.__new__(GitHubPRDiffService)
    service._github_api = api  # type: ignore[attr-defined]
    service._file_processor = processor
    service._diff_generator = generator
    service._logger = SimpleNamespace(error=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None, warning=lambda *a, **k: None)
    service._pr_diff_request_timeout_seconds = 180.0
    service._github_timeout_seconds = 30
    service._parallel_file_fetch_enabled = True
    service._max_concurrent = 2
    service._session_reader = None

    reader = GitHubSessionPRDiffReader(
        service,
        github_timeout_seconds=30,
        request_timeout_seconds=180.0,
        parallel_file_fetch_enabled=True,
        max_concurrent=2,
    )
    return reader, MemoryCache(), api, repo


def _standard_trees_blobs() -> tuple[dict[str, list[Any]], dict[str, bytes]]:
    base_tree = [
        _tree_item("a.py", "100644", "blob", BLOB_A),
        _tree_item("c.py", "100644", "blob", BLOB_C),
        _tree_item("old.py", "100644", "blob", BLOB_OLD),
        _tree_item("mode.sh", "100644", "blob", BLOB_A),
        _tree_item("link", "120000", "blob", BLOB_LINK),
    ]
    head_tree = [
        _tree_item("a.py", "100644", "blob", BLOB_B),
        _tree_item("b.py", "100644", "blob", BLOB_B),
        _tree_item("new.py", "100644", "blob", BLOB_NEW),
        _tree_item("mode.sh", "100755", "blob", BLOB_A),
        _tree_item("empty.py", "100644", "blob", BLOB_EMPTY),
        _tree_item("link", "120000", "blob", BLOB_LINK),
        _tree_item("sub", "160000", "commit", GITLINK),
    ]
    trees = {MERGE_BASE: base_tree, HEAD: head_tree}
    blobs = {
        BLOB_A: b"line1\n",
        BLOB_B: b"line1\nchanged\n",
        BLOB_C: b"gone\n",
        BLOB_OLD: b"same\n",
        BLOB_NEW: b"same\n",
        BLOB_LINK: b"../target",
        BLOB_EMPTY: b"",
    }
    return trees, blobs


@pytest.mark.integration
@pytest.mark.anyio
async def test_success_mixed_statuses_ordered_and_cache_hit() -> None:
    trees, blobs = _standard_trees_blobs()
    files = [
        FakeFile("a.py", "modified", additions=1, deletions=0),
        FakeFile("b.py", "added", additions=1),
        FakeFile("c.py", "removed", deletions=1),
        FakeFile("new.py", "renamed", previous_filename="old.py", additions=0, deletions=0),
        FakeFile("mode.sh", "modified", additions=0, deletions=0),
        FakeFile("empty.py", "added", additions=0),
        FakeFile("link", "modified", additions=1, deletions=1),
        FakeFile("sub", "added", additions=1),
    ]
    reader, cache, api, repo = _build_stack(files=files, trees=trees, blobs=blobs)
    use_case = GetPRDiffUseCase(reader, cache)

    result = await use_case.execute("acme", "demo", 7)
    assert result is not None
    assert [f.path for f in result.files] == [
        "a.py",
        "b.py",
        "c.py",
        "new.py",
        "mode.sh",
        "empty.py",
        "link",
        "sub",
    ]
    assert result.files[0].status is EDIT_TYPE.MODIFIED
    assert "changed" in result.files[0].diff
    assert result.files[1].status is EDIT_TYPE.ADDED
    assert result.files[2].status is EDIT_TYPE.DELETED
    assert result.files[3].status is EDIT_TYPE.RENAMED
    assert result.files[3].previous_path == "old.py"
    assert "old mode 100644" in result.files[4].diff
    assert "new mode 100755" in result.files[4].diff
    assert result.files[5].status is EDIT_TYPE.ADDED
    assert "new file mode 120000" in result.files[6].diff or "old mode 120000" in result.files[6].diff or "../target" in result.files[6].diff
    assert result.files[7].diff.startswith("new file mode 160000")
    assert f"Subproject commit {GITLINK}" in result.files[7].diff
    assert cache.sets == 1
    identity = github_full_diff_v3_identity("acme", "demo", 7, MERGE_BASE, HEAD)
    assert (identity.cache_key, identity.validation_token) in cache.store

    tree_calls_after_miss = len(repo.tree_calls)
    compare_calls_after_miss = len(repo.compare_calls)

    hit = await use_case.execute("acme", "demo", 7)
    assert hit is not None
    assert [f.path for f in hit.files] == [f.path for f in result.files]
    assert cache.sets == 1
    # Cache hit still opens a session (identity), but must not rebuild content/trees.
    assert len(repo.tree_calls) == tree_calls_after_miss
    # Open still compares once per open for identity capture.
    assert len(repo.compare_calls) >= compare_calls_after_miss


@pytest.mark.integration
@pytest.mark.anyio
async def test_zero_file_success_is_cached() -> None:
    trees = {MERGE_BASE: [], HEAD: []}
    blobs: dict[str, bytes] = {}
    reader, cache, _api, _repo = _build_stack(files=[], trees=trees, blobs=blobs, changed_files=0)
    use_case = GetPRDiffUseCase(reader, cache)
    result = await use_case.execute("acme", "demo", 1)
    assert result is not None
    assert result.files == ()
    assert cache.sets == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_page_two_inventory_failure_no_cache() -> None:
    trees, blobs = _standard_trees_blobs()

    class PageTwoPR:
        def __init__(self) -> None:
            self.base = SimpleNamespace(sha=BASE_TIP)
            self.head = SimpleNamespace(sha=HEAD)
            self.changed_files = 2

        def get_files(self):
            yield FakeFile("a.py", "modified")
            raise RuntimeError("page two failed")

    pr = PageTwoPR()
    repo = FakeRepo(trees=trees, blobs=blobs, pr=pr)
    api = FakeAPI(repo, pr)
    pattern = SimpleNamespace(is_valid_file=lambda path: True)
    processor = FileProcessor(pattern_matcher=pattern)  # type: ignore[arg-type]
    service = GitHubPRDiffService.__new__(GitHubPRDiffService)
    service._github_api = api  # type: ignore[attr-defined]
    service._file_processor = processor
    service._diff_generator = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)
    service._logger = SimpleNamespace(error=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None, warning=lambda *a, **k: None)
    service._pr_diff_request_timeout_seconds = 180.0
    service._github_timeout_seconds = 30
    service._parallel_file_fetch_enabled = True
    service._max_concurrent = 1
    service._session_reader = None
    reader = GitHubSessionPRDiffReader(service, max_concurrent=1)
    cache = MemoryCache()
    use_case = GetPRDiffUseCase(reader, cache)

    with pytest.raises(RuntimeError, match="page two"):
        await use_case.execute("acme", "demo", 9)
    assert cache.sets == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_snapshot_drift_no_cache() -> None:
    trees, blobs = _standard_trees_blobs()
    files = [FakeFile("a.py", "modified")]
    revalidate = SimpleNamespace(
        base=SimpleNamespace(sha=BASE_TIP),
        head=SimpleNamespace(sha=HEAD2),
        changed_files=1,
    )
    reader, cache, _api, repo = _build_stack(
        files=files,
        trees=trees,
        blobs=blobs,
        revalidate_pr=revalidate,
    )
    # Revalidation compare must still return same merge base unless we change it —
    # head drift alone is enough for SNAPSHOT_CHANGED.
    use_case = GetPRDiffUseCase(reader, cache)
    with pytest.raises(FullDiffIncompleteError) as ei:
        await use_case.execute("acme", "demo", 3)
    assert ei.value.reason is FullDiffIncompleteReason.SNAPSHOT_CHANGED
    assert cache.sets == 0
    assert repo.get_pull_calls >= 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_compare_failure_is_operational_no_cache() -> None:
    from github import GithubException

    trees, blobs = _standard_trees_blobs()
    files = [FakeFile("a.py", "modified")]
    reader, cache, _api, _repo = _build_stack(
        files=files,
        trees=trees,
        blobs=blobs,
        compare_error=GithubException(500, {"message": "boom"}, None),
    )
    use_case = GetPRDiffUseCase(reader, cache)
    with pytest.raises(GitHubAPIError) as ei:
        await use_case.execute("acme", "demo", 2)
    assert not isinstance(ei.value, FullDiffIncompleteError)
    assert cache.sets == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_truncated_tree_no_cache() -> None:
    files = [FakeFile("a.py", "modified")]

    class TruncRepo(FakeRepo):
        def get_git_tree(self, sha: str, recursive: bool = False) -> Any:
            self.tree_calls.append(sha)
            return SimpleNamespace(truncated=True, tree=[])

    pr = SimpleNamespace(
        base=SimpleNamespace(sha=BASE_TIP),
        head=SimpleNamespace(sha=HEAD),
        changed_files=1,
        get_files=lambda: files,
    )
    # wrap get_files
    pr.get_files = lambda: list(files)  # type: ignore[method-assign]
    repo = TruncRepo(trees={}, blobs={}, pr=pr)
    api = FakeAPI(repo, pr)
    processor = FileProcessor(
        pattern_matcher=SimpleNamespace(is_valid_file=lambda p: True),  # type: ignore[arg-type]
    )
    service = GitHubPRDiffService.__new__(GitHubPRDiffService)
    service._github_api = api  # type: ignore[attr-defined]
    service._file_processor = processor
    service._diff_generator = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)
    service._logger = SimpleNamespace(error=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None, warning=lambda *a, **k: None)
    service._pr_diff_request_timeout_seconds = 180.0
    service._github_timeout_seconds = 30
    service._parallel_file_fetch_enabled = True
    service._max_concurrent = 1
    service._session_reader = None
    reader = GitHubSessionPRDiffReader(service, max_concurrent=1)
    cache = MemoryCache()
    with pytest.raises(FullDiffIncompleteError) as ei:
        await GetPRDiffUseCase(reader, cache).execute("acme", "demo", 4)
    assert ei.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED
    assert cache.sets == 0
