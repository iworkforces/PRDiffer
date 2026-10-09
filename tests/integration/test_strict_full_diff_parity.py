"""Serial vs parallel full-diff public output byte equivalence."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import pytest

from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.usecases.pr_diff_usecases import GetPRDiffUseCase
from tests.integration.test_github_strict_full_diff import (
    BASE_TIP,
    FakeFile,
    HEAD,
    MemoryCache,
    _build_stack,
    _standard_trees_blobs,
)
from tests.integration.test_gitlab_strict_full_diff import (
    _build_reader,
    _record,
)
from prdiffer.infrastructure.vcs_providers.gitlab_models import GitLabDiffSnapshot


def _canonical_prdiff_bytes(diff: PRDiff) -> bytes:
    payload = asdict(diff)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _file_diff_bytes(diff: PRDiff) -> list[bytes]:
    return [item.diff.encode("utf-8") for item in diff.files]


@pytest.mark.integration
@pytest.mark.anyio
async def test_github_serial_parallel_byte_equivalent() -> None:
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

    async def run(*, parallel: bool) -> PRDiff:
        reader, cache, _api, _repo = _build_stack(files=files, trees=trees, blobs=blobs)
        # max_concurrent encodes parallel capacity on session reader / service
        reader._limiter = __import__("anyio").CapacityLimiter(4 if parallel else 1)
        reader._service._parallel_file_fetch_enabled = parallel
        reader._service._max_concurrent = 4 if parallel else 1
        result = await GetPRDiffUseCase(reader, cache).execute("acme", "demo", 11)
        assert result is not None
        return result

    serial = await run(parallel=False)
    parallel = await run(parallel=True)
    assert serial == parallel
    assert _canonical_prdiff_bytes(serial) == _canonical_prdiff_bytes(parallel)
    assert _file_diff_bytes(serial) == _file_diff_bytes(parallel)


@pytest.mark.integration
@pytest.mark.anyio
async def test_gitlab_serial_parallel_byte_equivalent() -> None:
    records = (
        _record("a.py", "a.py"),
        _record("b.py", "b.py", new_file=True),
        _record("c.py", "c.py", deleted_file=True),
        _record("old.py", "new.py", renamed_file=True),
        _record("mode.sh", "mode.sh", a_mode="100644", b_mode="100755"),
        _record("empty.py", "empty.py", new_file=True),
        _record("plus.py", "plus.py"),
    )
    snap = GitLabDiffSnapshot(
        project_path="g/p",
        iid=12,
        version_id=3,
        base_sha="base",
        start_sha="start",
        head_sha="head",
        state="collected",
        real_size=len(records),
        records=records,
    )
    store = {
        ("a.py", "base"): b"line1\n",
        ("a.py", "head"): b"line1\nchanged\n",
        ("b.py", "head"): b"new\n",
        ("c.py", "base"): b"gone\n",
        ("old.py", "base"): b"same\n",
        ("new.py", "head"): b"same\n",
        ("mode.sh", "base"): b"echo\n",
        ("mode.sh", "head"): b"echo\n",
        ("empty.py", "head"): b"",
        ("plus.py", "base"): b"--old\n",
        ("plus.py", "head"): b"++new\n",
    }

    async def run(*, parallel: bool) -> PRDiff:
        reader, _ops, cache, _client = _build_reader(snap, store, parallel_enabled=parallel)
        result = await GetPRDiffUseCase(reader, cache).execute("g", "p", 12)
        assert result is not None
        return result

    serial = await run(parallel=False)
    parallel = await run(parallel=True)
    assert serial == parallel
    assert _canonical_prdiff_bytes(serial) == _canonical_prdiff_bytes(parallel)
    assert _file_diff_bytes(serial) == _file_diff_bytes(parallel)
    # ++/-- source lines counted once each
    plus = next(f for f in serial.files if f.path == "plus.py")
    assert plus.stats.additions == 1
    assert plus.stats.deletions == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_github_failure_mode_matches_serial_parallel() -> None:
    """Truncated tree fails with same E5020 reason in both capacity modes."""
    from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
    from tests.integration.test_github_strict_full_diff import FakeAPI, FakeRepo
    from prdiffer.infrastructure.github.diff_generator import DiffGenerator
    from prdiffer.infrastructure.github.file_processor import FileProcessor
    from prdiffer.infrastructure.github.pr_diff_session import GitHubSessionPRDiffReader
    from prdiffer.infrastructure.services.pr_diff_service import GitHubPRDiffService
    from prdiffer.infrastructure.utils.diff_utils import DiffUtils
    from types import SimpleNamespace

    files = [FakeFile("a.py", "modified")]

    async def run(*, capacity: int) -> FullDiffIncompleteReason:
        pr = SimpleNamespace(
            base=SimpleNamespace(sha=BASE_TIP),
            head=SimpleNamespace(sha=HEAD),
            changed_files=1,
            get_files=lambda: list(files),
        )

        class TruncRepo(FakeRepo):
            def get_git_tree(self, sha: str, recursive: bool = False) -> Any:
                self.tree_calls.append(sha)
                return SimpleNamespace(truncated=True, tree=[])

        repo = TruncRepo(trees={}, blobs={}, pr=pr)
        api = FakeAPI(repo, pr)
        processor = FileProcessor(
            pattern_matcher=SimpleNamespace(is_valid_file=lambda p: True),  # type: ignore[arg-type]
        )
        service = GitHubPRDiffService.__new__(GitHubPRDiffService)
        service._github_api = api  # type: ignore[attr-defined]
        service._file_processor = processor
        service._diff_generator = DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False)
        service._logger = SimpleNamespace(
            error=lambda *a, **k: None,
            info=lambda *a, **k: None,
            debug=lambda *a, **k: None,
            warning=lambda *a, **k: None,
        )
        service._pr_diff_request_timeout_seconds = 180.0
        service._github_timeout_seconds = 30
        service._parallel_file_fetch_enabled = capacity > 1
        service._max_concurrent = capacity
        service._session_reader = None
        reader = GitHubSessionPRDiffReader(service, max_concurrent=capacity)
        cache = MemoryCache()
        with pytest.raises(FullDiffIncompleteError) as ei:
            await GetPRDiffUseCase(reader, cache).execute("acme", "demo", 99)
        assert cache.sets == 0
        return ei.value.reason

    assert await run(capacity=1) is FullDiffIncompleteReason.INVENTORY_TRUNCATED
    assert await run(capacity=4) is FullDiffIncompleteReason.INVENTORY_TRUNCATED


@pytest.mark.integration
@pytest.mark.anyio
async def test_gitlab_failure_mode_matches_serial_parallel() -> None:
    """Incomplete tree fails with same E5020 reason in serial and parallel capacity."""
    from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason

    records = (_record("link", "link", a_mode="120000", b_mode="120000"),)
    snap = GitLabDiffSnapshot(
        project_path="g/p",
        iid=77,
        version_id=9,
        base_sha="base",
        start_sha="start",
        head_sha="head",
        state="collected",
        real_size=1,
        records=records,
    )

    class TruncTree:
        next_page = 2

        def __iter__(self):
            return iter([])

    incomplete = TruncTree()
    trees = {"base": incomplete, "head": incomplete, "start": incomplete}
    store: dict[tuple[str, str], bytes] = {}

    async def run(*, parallel: bool) -> FullDiffIncompleteReason:
        reader, _ops, cache, _client = _build_reader(
            snap,
            store,
            trees=trees,
            parallel_enabled=parallel,
        )
        with pytest.raises(FullDiffIncompleteError) as ei:
            await GetPRDiffUseCase(reader, cache).execute("g", "p", 77)
        assert cache.sets == 0
        return ei.value.reason

    assert await run(parallel=False) is FullDiffIncompleteReason.INVENTORY_TRUNCATED
    assert await run(parallel=True) is FullDiffIncompleteReason.INVENTORY_TRUNCATED
