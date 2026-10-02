"""Strict cache identity and no-write-on-failure contract (use-case level)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import (
    StrictPRDiffCacheIdentity,
    github_full_diff_v3_identity,
    gitlab_full_diff_v1_identity,
    unwrap_pr_diff_cache_value,
)
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
from prdiffer.domain.usecases.pr_diff_usecases import GetPRDiffUseCase

_MB = "b" * 40
_HD = "c" * 40
_BT = "a" * 40
_MB2 = "e" * 40


def _pr() -> PRDiff:
    return PRDiff(
        files=(
            FileDiffResponse(
                path="a.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=1, deletions=0),
                diff="+x\n",
            ),
        )
    )


class RecordingCache:
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


class FakeSession:
    def __init__(self, identity: StrictPRDiffCacheIdentity, build: AsyncMock) -> None:
        self.cache_identity = identity
        self.snapshot = PRDiffSnapshot("o", "r", 1, _BT, _MB, _HD, 1)
        self.build = build
        self.closed = False

    async def build_pr_diff(self) -> PRDiff:
        return await self.build()

    async def aclose(self) -> None:
        self.closed = True


class SessionReader:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.opens = 0

    async def open_pr_diff_session(
        self,
        owner: str,
        repo: str,
        pr: int,
        /,
        *,
        base_url: str | None = None,
    ) -> FakeSession:
        self.opens += 1
        return self.session


@pytest.mark.integration
@pytest.mark.anyio
async def test_github_v3_hit_and_miss() -> None:
    cache = RecordingCache()
    identity = github_full_diff_v3_identity("o", "r", 1, _MB, _HD)
    value = _pr()
    # Preload unrelated key — must not hit v3 identity
    cache.store[("unrelated:key", "token")] = value

    build = AsyncMock(return_value=value)
    session = FakeSession(identity, build)
    reader = SessionReader(session)
    use_case = GetPRDiffUseCase(reader, cache)

    result = await use_case.execute("o", "r", 1)
    assert result is value
    assert build.await_count == 1
    assert cache.sets == 1
    assert session.closed is True

    # Hit under exact v3 identity
    build2 = AsyncMock()
    session2 = FakeSession(identity, build2)
    reader2 = SessionReader(session2)
    hit = await GetPRDiffUseCase(reader2, cache).execute("o", "r", 1)
    assert hit is value
    build2.assert_not_awaited()
    assert cache.sets == 1
    assert session2.closed is True


@pytest.mark.integration
@pytest.mark.anyio
async def test_different_merge_base_rebuilds() -> None:
    cache = RecordingCache()
    id_a = github_full_diff_v3_identity("o", "r", 1, _MB, _HD)
    id_b = github_full_diff_v3_identity("o", "r", 1, _MB2, _HD)
    assert id_a.cache_key != id_b.cache_key

    build_a = AsyncMock(return_value=_pr())
    await GetPRDiffUseCase(SessionReader(FakeSession(id_a, build_a)), cache).execute("o", "r", 1)
    assert cache.sets == 1

    build_b = AsyncMock(return_value=_pr())
    await GetPRDiffUseCase(SessionReader(FakeSession(id_b, build_b)), cache).execute("o", "r", 1)
    assert build_b.await_count == 1
    assert cache.sets == 2


@pytest.mark.integration
@pytest.mark.anyio
async def test_e5020_and_empty_cache_semantics() -> None:
    cache = RecordingCache()
    identity = github_full_diff_v3_identity("o", "r", 1, _MB, _HD)
    build = AsyncMock(side_effect=FullDiffIncompleteError(FullDiffIncompleteReason.SNAPSHOT_CHANGED))
    session = FakeSession(identity, build)
    with pytest.raises(FullDiffIncompleteError):
        await GetPRDiffUseCase(SessionReader(session), cache).execute("o", "r", 1)
    assert cache.sets == 0
    assert session.closed is True

    # Empty success is cacheable (PRDiff truthy)
    empty = PRDiff(files=())
    build_ok = AsyncMock(return_value=empty)
    session_ok = FakeSession(identity, build_ok)
    result = await GetPRDiffUseCase(SessionReader(session_ok), cache).execute("o", "r", 1)
    assert result is empty
    assert cache.sets == 1


@pytest.mark.integration
def test_unwrap_accepts_only_v3_github_keys() -> None:
    value = _pr()
    v3 = github_full_diff_v3_identity("o", "r", 1, _MB, _HD)
    assert unwrap_pr_diff_cache_value(value, key="not-a-strict-key:o:r:1:h", identity=v3) is None
    assert unwrap_pr_diff_cache_value(value, key=v3.cache_key, identity=v3) is value


@pytest.mark.integration
@pytest.mark.anyio
async def test_gitlab_identity_stable_and_closes() -> None:
    cache = RecordingCache()
    identity = gitlab_full_diff_v1_identity("ns", "repo", 1, 9, "b", "s", "h", host="gitlab.example.com")
    build = AsyncMock(return_value=_pr())
    session = FakeSession(identity, build)
    result = await GetPRDiffUseCase(SessionReader(session), cache).execute("ns", "repo", 1)
    assert result is not None
    assert cache.sets == 1
    assert session.closed is True
    # Hit
    build2 = AsyncMock()
    session2 = FakeSession(identity, build2)
    hit = await GetPRDiffUseCase(SessionReader(session2), cache).execute("ns", "repo", 1)
    assert hit is result
    build2.assert_not_awaited()
    assert session2.closed is True
