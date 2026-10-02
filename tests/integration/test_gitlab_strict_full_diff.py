"""No-network GitLab strict full-diff integration matrix."""

from __future__ import annotations

from typing import Any

import pytest

from prdiffer.domain.config.gitlab_config import GitLabConfig
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff_cache import gitlab_full_diff_v1_identity
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.domain.usecases.pr_diff_usecases import GetPRDiffUseCase
from prdiffer.infrastructure.github.diff_generator import DiffGenerator
from prdiffer.infrastructure.utils.diff_utils import DiffUtils
from prdiffer.infrastructure.vcs_providers.gitlab_content import GitLabContentFetcher
from prdiffer.infrastructure.vcs_providers.gitlab_diff_generator import GitLabDiffAssembler
from prdiffer.infrastructure.vcs_providers.gitlab_diff_session import GitLabSessionPRDiffReader
from prdiffer.infrastructure.vcs_providers.gitlab_models import GitLabDiffRecord, GitLabDiffSnapshot
from prdiffer.infrastructure.vcs_providers.gitlab_operations import GitLabOperations
from prdiffer.infrastructure.vcs_providers.gitlab_runtime import GitLabRuntime


class MemoryCache:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], Any] = {}
        self.sets = 0

    def get_cache_key(self, owner: str, repo: str, pr: int) -> str:
        return f"{owner}/{repo}/{pr}"

    async def get_optimistic(self, key: str) -> tuple[Any, None]:
        return None, None

    async def get(self, key: str, token: str) -> Any:
        return self.store.get((key, token))

    async def set(self, key: str, token: str, value: Any) -> None:
        self.sets += 1
        self.store[(key, token)] = value


class FakeOps(GitLabOperations):
    def __init__(self, snapshot: GitLabDiffSnapshot) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.select_calls = 0

    def select_with_client(
        self,
        client: object,
        project_path: str,
        iid: int,
    ) -> GitLabDiffSnapshot:
        # Session path uses runtime.run_blocking → select_with_client.
        _ = client
        self.select_calls += 1
        return self.snapshot


class FakeClient:
    def __init__(
        self,
        files: dict[tuple[str, str], bytes],
        *,
        trees: dict[str, list[Any]] | None = None,
        blobs: dict[str, bytes] | None = None,
    ) -> None:
        self._files = files
        self._trees = trees or {}
        self._blobs = blobs or {}
        self.raw_blob_calls: list[str] = []
        self.tree_calls: list[str] = []
        self.raw_file_calls: list[tuple[str, str]] = []
        self.session = type("S", (), {"close": lambda self: None})()
        outer = self

        class _Files:
            def __init__(self, store: dict[tuple[str, str], bytes]) -> None:
                self.store = store
                self.calls: list[tuple[str, str]] = []

            def raw(self, file_path: str, ref: str) -> bytes:
                self.calls.append((file_path, ref))
                outer.raw_file_calls.append((file_path, ref))
                if (file_path, ref) not in self.store:
                    import gitlab

                    raise gitlab.GitlabGetError("nf", response_code=404)
                return self.store[(file_path, ref)]

        class _Project:
            def __init__(
                self,
                store: dict[tuple[str, str], bytes],
                trees: dict[str, list[Any]],
                blobs: dict[str, bytes],
            ) -> None:
                self.files = _Files(store)
                self._trees = trees
                self._blobs = blobs

            def repository_tree(self, ref: str = "", recursive: bool = False, get_all: bool = False, **kwargs: Any) -> Any:
                outer.tree_calls.append(ref)
                raw = self._trees.get(ref, [])
                # Preserve incomplete-paginator fakes (e.g. next_page set) for fail-closed tests.
                if isinstance(raw, list):
                    return list(raw)
                return raw

            def repository_raw_blob(self, sha: str) -> bytes:
                outer.raw_blob_calls.append(sha)
                if sha not in self._blobs:
                    import gitlab

                    raise gitlab.GitlabGetError("blob nf", response_code=404)
                return self._blobs[sha]

        class _Projects:
            def __init__(
                self,
                store: dict[tuple[str, str], bytes],
                trees: dict[str, list[Any]],
                blobs: dict[str, bytes],
            ) -> None:
                self._store = store
                self._trees = trees
                self._blobs = blobs

            def get(self, path: str) -> Any:
                return _Project(self._store, self._trees, self._blobs)

        self.projects = _Projects(files, self._trees, self._blobs)


def _record(
    old: str,
    new: str,
    *,
    new_file: bool = False,
    deleted_file: bool = False,
    renamed_file: bool = False,
    a_mode: str | None = "100644",
    b_mode: str | None = "100644",
    collapsed: bool = False,
    too_large: bool = False,
) -> GitLabDiffRecord:
    return GitLabDiffRecord(
        old_path=old,
        new_path=new,
        new_file=new_file,
        deleted_file=deleted_file,
        renamed_file=renamed_file,
        a_mode=a_mode,
        b_mode=b_mode,
        diff=None,
        collapsed=collapsed,
        too_large=too_large,
    )


def _build_reader(
    snapshot: GitLabDiffSnapshot,
    file_store: dict[tuple[str, str], bytes],
    *,
    trees: dict[str, list[Any]] | None = None,
    blobs: dict[str, bytes] | None = None,
    parallel_enabled: bool = True,
) -> tuple[GitLabSessionPRDiffReader, FakeOps, MemoryCache, FakeClient]:
    config = GitLabConfig()
    client = FakeClient(file_store, trees=trees, blobs=blobs)
    runtime = GitLabRuntime(config, client_factory=lambda *a, **k: client)
    ops = FakeOps(snapshot)
    content = GitLabContentFetcher(runtime, config, parallel_enabled=parallel_enabled)
    assembler = GitLabDiffAssembler(DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False), config)
    reader = GitLabSessionPRDiffReader(
        operations=ops,
        runtime=runtime,
        content_fetcher=content,
        assembler=assembler,
        config=config,
    )
    return reader, ops, MemoryCache(), client


@pytest.mark.integration
@pytest.mark.anyio
async def test_strict_mixed_file_success_ordered() -> None:
    records = (
        _record("a.py", "a.py"),
        _record("b.py", "b.py", new_file=True),
        _record("c.py", "c.py", deleted_file=True),
        _record("old.py", "new.py", renamed_file=True),
        _record("mode.sh", "mode.sh", a_mode="100644", b_mode="100755"),
        _record("empty.py", "empty.py", new_file=True),
        _record("col.py", "col.py", collapsed=True),
    )
    snap = GitLabDiffSnapshot(
        project_path="group/sub/project",
        iid=42,
        version_id=9,
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
        ("col.py", "base"): b"x\n",
        ("col.py", "head"): b"y\n",
    }
    reader, ops, cache, _client = _build_reader(snap, store)
    use_case = GetPRDiffUseCase(reader, cache)
    result = await use_case.execute("group/sub", "project", 42)
    assert result is not None
    assert [f.path for f in result.files] == ["a.py", "b.py", "c.py", "new.py", "mode.sh", "empty.py", "col.py"]
    assert result.files[3].previous_path == "old.py"
    assert result.files[3].status is EDIT_TYPE.RENAMED
    assert "old mode 100644" in result.files[4].diff
    assert "line1" in result.files[0].diff
    assert cache.sets == 1
    assert ops.select_calls == 1

    # Cache hit: no second build (select still opens session once more)
    hit = await use_case.execute("group/sub", "project", 42)
    assert hit is not None
    assert [f.path for f in hit.files] == [f.path for f in result.files]
    assert cache.sets == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_incomplete_binary_no_cache_write() -> None:
    records = (_record("bin.dat", "bin.dat", new_file=True),)
    snap = GitLabDiffSnapshot(
        project_path="o/r",
        iid=1,
        version_id=1,
        base_sha="b",
        start_sha="s",
        head_sha="h",
        state="collected",
        real_size=1,
        records=records,
    )
    store = {("bin.dat", "h"): b"a\x00b"}
    reader, _, cache, _client = _build_reader(snap, store)
    use_case = GetPRDiffUseCase(reader, cache)
    with pytest.raises(FullDiffIncompleteError) as exc:
        await use_case.execute("o", "r", 1)
    assert exc.value.reason is FullDiffIncompleteReason.BINARY_CONTENT
    assert cache.sets == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_legacy_cache_key_does_not_hit_strict_identity() -> None:
    records = (_record("a.py", "a.py", new_file=True),)
    snap = GitLabDiffSnapshot(
        project_path="o/r",
        iid=1,
        version_id=2,
        base_sha="b",
        start_sha="s",
        head_sha="h",
        state="collected",
        real_size=1,
        records=records,
    )
    store = {("a.py", "h"): b"hi\n"}
    reader, ops, cache, _client = _build_reader(snap, store)
    # Preload legacy-style wrong key (must not satisfy strict identity lookup)
    cache.store[("gitlab:o:r:1", "h")] = object()
    use_case = GetPRDiffUseCase(reader, cache)
    result = await use_case.execute("o", "r", 1)
    assert result is not None
    identity = gitlab_full_diff_v1_identity("o", "r", 1, 2, "b", "s", "h")
    assert (identity.cache_key, identity.validation_token) in cache.store
    assert ops.select_calls >= 1


def _tree(path: str, mode: str, otype: str, oid: str) -> dict[str, str]:
    return {"path": path, "mode": mode, "type": otype, "id": oid}


@pytest.mark.integration
@pytest.mark.anyio
async def test_symlink_and_gitlink_use_tree_not_raw_file() -> None:
    link_oid = "a" * 40
    git_oid = "b" * 40
    records = (
        _record("link", "link", new_file=True, a_mode=None, b_mode="120000"),
        _record("sub", "sub", new_file=True, a_mode=None, b_mode="160000"),
    )
    snap = GitLabDiffSnapshot(
        project_path="o/r",
        iid=5,
        version_id=1,
        base_sha="base",
        start_sha="start",
        head_sha="head",
        state="collected",
        real_size=2,
        records=records,
    )
    trees = {
        "head": [
            _tree("link", "120000", "blob", link_oid),
            _tree("sub", "160000", "commit", git_oid),
        ],
        "base": [],
    }
    blobs = {link_oid: b"../target"}
    reader, ops, cache, client = _build_reader(snap, {}, trees=trees, blobs=blobs)
    use_case = GetPRDiffUseCase(reader, cache)
    result = await use_case.execute("o", "r", 5)
    assert result is not None
    assert [f.path for f in result.files] == ["link", "sub"]
    assert "../target" in result.files[0].diff
    assert result.files[0].diff.startswith("new file mode 120000")
    assert f"Subproject commit {git_oid}" in result.files[1].diff
    assert result.files[1].diff.startswith("new file mode 160000")
    assert cache.sets == 1
    # files.raw must not be used for these modes
    assert client.raw_file_calls == []
    assert link_oid in client.raw_blob_calls
    assert git_oid not in client.raw_blob_calls  # gitlink synthesizes text


@pytest.mark.integration
@pytest.mark.anyio
async def test_double_plus_minus_source_stats() -> None:
    records = (_record("a.py", "a.py"),)
    snap = GitLabDiffSnapshot(
        project_path="o/r",
        iid=6,
        version_id=1,
        base_sha="base",
        start_sha="start",
        head_sha="head",
        state="collected",
        real_size=1,
        records=records,
    )
    # Content lines that start with ++ / -- once prefixed in unified output
    store = {
        ("a.py", "base"): b"--old\n",
        ("a.py", "head"): b"++new\n",
    }
    reader, _ops, cache, _client = _build_reader(snap, store)
    result = await GetPRDiffUseCase(reader, cache).execute("o", "r", 6)
    assert result is not None
    f = result.files[0]
    assert f.stats.deletions == 1
    assert f.stats.additions == 1
    assert cache.sets == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_ops_failure_no_cache_write() -> None:
    class BoomOps(FakeOps):
        def select_with_client(self, client: object, project_path: str, iid: int) -> GitLabDiffSnapshot:
            self.select_calls += 1
            raise FullDiffIncompleteError(
                FullDiffIncompleteReason.INVENTORY_TRUNCATED,
                message="version list incomplete",
            )

    records = (_record("a.py", "a.py", new_file=True),)
    snap = GitLabDiffSnapshot(
        project_path="o/r",
        iid=7,
        version_id=1,
        base_sha="b",
        start_sha="s",
        head_sha="h",
        state="collected",
        real_size=1,
        records=records,
    )
    config = GitLabConfig()
    client = FakeClient({("a.py", "h"): b"x\n"})
    runtime = GitLabRuntime(config, client_factory=lambda *a, **k: client)
    ops = BoomOps(snap)
    content = GitLabContentFetcher(runtime, config)
    assembler = GitLabDiffAssembler(DiffGenerator(diff_utils=DiffUtils(), parallel_enabled=False), config)
    reader = GitLabSessionPRDiffReader(
        operations=ops,
        runtime=runtime,
        content_fetcher=content,
        assembler=assembler,
        config=config,
    )
    cache = MemoryCache()
    with pytest.raises(FullDiffIncompleteError) as ei:
        await GetPRDiffUseCase(reader, cache).execute("o", "r", 7)
    assert ei.value.reason is FullDiffIncompleteReason.INVENTORY_TRUNCATED
    assert cache.sets == 0
    assert ops.select_calls == 1
