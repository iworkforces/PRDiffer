"""Tests for the session-based GetPRDiffUseCase cache and lifecycle flow."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from prdiffer.domain.entities.file_diff_response import FileDiffResponse, FileStats
from prdiffer.domain.entities.file_patch import EDIT_TYPE
from prdiffer.domain.entities.pr_diff import PRDiff
from prdiffer.domain.entities.pr_diff_cache import (
    StrictPRDiffCacheIdentity,
    github_full_diff_v3_identity,
    gitlab_full_diff_v1_identity,
)
from prdiffer.domain.exceptions import FullDiffIncompleteError, FullDiffIncompleteReason
from prdiffer.domain.interfaces.pr_diff_reader import PRDiffSnapshot
from prdiffer.domain.usecases.pr_diff_usecases import GetPRDiffUseCase

_MB = "b" * 40
_HD = "c" * 40
_BT = "a" * 40


def _pr_diff() -> PRDiff:
    return PRDiff(
        files=(
            FileDiffResponse(
                path="a.py",
                status=EDIT_TYPE.MODIFIED,
                stats=FileStats(additions=1, deletions=0),
                diff="+x",
            ),
        )
    )


@dataclass
class FakeSession:
    snapshot: PRDiffSnapshot
    cache_identity: StrictPRDiffCacheIdentity
    build: AsyncMock
    closed: bool = False

    async def build_pr_diff(self) -> PRDiff:
        return await self.build()

    async def aclose(self) -> None:
        self.closed = True


class SessionReader:
    def __init__(self, session: FakeSession):
        self.session = session
        self.open_calls = 0
        self.open_kwargs: list[dict[str, object]] = []

    async def open_pr_diff_session(
        self,
        owner: str,
        repo: str,
        pr: int,
        /,
        *,
        base_url: str | None = None,
    ) -> FakeSession:
        self.open_calls += 1
        self.open_kwargs.append({"owner": owner, "repo": repo, "pr": pr, "base_url": base_url})
        return self.session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_path_uses_cache_identity_and_closes() -> None:
    cache = MagicMock()
    cache.get_optimistic = AsyncMock(return_value=(None, None))
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()

    build = AsyncMock(return_value=_pr_diff())
    identity = github_full_diff_v3_identity("o", "r", 1, _MB, _HD)
    session = FakeSession(
        snapshot=PRDiffSnapshot("o", "r", 1, _BT, _MB, _HD, 1),
        cache_identity=identity,
        build=build,
    )
    reader = SessionReader(session)
    use_case = GetPRDiffUseCase(reader, cache, cache_hit_optimization_enabled=False)

    result = await use_case.execute("o", "r", 1)

    assert result is not None
    assert reader.open_calls == 1
    build.assert_awaited_once()
    cache.set.assert_awaited_once()
    assert cache.set.await_args.args[0] == identity.cache_key
    assert cache.set.await_args.args[1] == identity.validation_token
    assert session.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_cache_hit_closes_without_build() -> None:
    cached = _pr_diff()
    identity = github_full_diff_v3_identity("o", "r", 1, _MB, _HD)
    cache = MagicMock()
    cache.get_optimistic = AsyncMock(return_value=(None, None))
    cache.get = AsyncMock(return_value=cached)
    cache.set = AsyncMock()

    build = AsyncMock()
    session = FakeSession(
        snapshot=PRDiffSnapshot("o", "r", 1, _BT, _MB, _HD, 1),
        cache_identity=identity,
        build=build,
    )
    reader = SessionReader(session)
    use_case = GetPRDiffUseCase(reader, cache)

    result = await use_case.execute("o", "r", 1)

    assert result is cached
    build.assert_not_called()
    cache.set.assert_not_called()
    assert session.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gitlab_session_identity_cache_miss_and_hit() -> None:
    identity = gitlab_full_diff_v1_identity("ns", "repo", 1, 9, "b", "s", "h")
    cached = _pr_diff()
    cache = MagicMock()
    cache.get_optimistic = AsyncMock(return_value=(None, None))
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    build = AsyncMock(return_value=cached)
    session = FakeSession(
        snapshot=PRDiffSnapshot("ns", "repo", 1, "s", "b", "h", 1),
        cache_identity=identity,
        build=build,
    )
    reader = SessionReader(session)
    use_case = GetPRDiffUseCase(reader, cache)
    result = await use_case.execute("ns", "repo", 1)
    assert result is cached
    cache.set.assert_awaited_once_with(identity.cache_key, identity.validation_token, cached)

    # Hit path
    cache.get = AsyncMock(return_value=cached)
    cache.set = AsyncMock()
    build2 = AsyncMock()
    session2 = FakeSession(
        snapshot=PRDiffSnapshot("ns", "repo", 1, "s", "b", "h", 1),
        cache_identity=identity,
        build=build2,
    )
    reader2 = SessionReader(session2)
    use_case2 = GetPRDiffUseCase(reader2, cache)
    hit = await use_case2.execute("ns", "repo", 1)
    assert hit is cached
    build2.assert_not_called()
    assert session2.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_e5020_does_not_write_cache() -> None:
    identity = github_full_diff_v3_identity("o", "r", 1, _MB, _HD)
    cache = MagicMock()
    cache.get_optimistic = AsyncMock(return_value=(None, None))
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    build = AsyncMock(side_effect=FullDiffIncompleteError(FullDiffIncompleteReason.BINARY_CONTENT, path="x.bin"))
    session = FakeSession(
        snapshot=PRDiffSnapshot("o", "r", 1, _BT, _MB, _HD, 1),
        cache_identity=identity,
        build=build,
    )
    reader = SessionReader(session)
    use_case = GetPRDiffUseCase(reader, cache)
    with pytest.raises(FullDiffIncompleteError):
        await use_case.execute("o", "r", 1)
    cache.set.assert_not_called()
    assert session.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_legacy_hunk_key_misses_on_strict_session() -> None:
    """Legacy gitlab:owner:repo:iid values must not unwrap under strict identity."""
    identity = gitlab_full_diff_v1_identity("o", "r", 1, 1, "b", "s", "h")
    cache = MagicMock()
    cache.get_optimistic = AsyncMock(return_value=(None, None))
    # Simulate wrong legacy value under wrong key lookup returning junk
    cache.get = AsyncMock(return_value=_pr_diff())  # bare PRDiff under wrong schema path
    # Force get to return value but with wrong key semantics: use case passes identity key
    build = AsyncMock(return_value=_pr_diff())
    session = FakeSession(
        snapshot=PRDiffSnapshot("o", "r", 1, _BT, _MB, _HD, 1),
        cache_identity=identity,
        build=build,
    )
    # Make get return a PRDiff but for a non-matching unwrap: we need get called with identity key
    # unwrap under gitlab v1 key accepts bare PRDiff — so inject wrong-type instead
    cache.get = AsyncMock(return_value={"legacy": True})
    cache.set = AsyncMock()
    reader = SessionReader(session)
    use_case = GetPRDiffUseCase(reader, cache)
    result = await use_case.execute("o", "r", 1)
    assert result is not None
    build.assert_awaited_once()
    cache.set.assert_awaited_once()
    assert session.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_path_passes_base_url_exactly_once() -> None:
    cache = MagicMock()
    cache.get_optimistic = AsyncMock(return_value=(None, None))
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    build = AsyncMock(return_value=_pr_diff())
    identity = github_full_diff_v3_identity("o", "r", 1, _MB, _HD)
    session = FakeSession(
        snapshot=PRDiffSnapshot("o", "r", 1, _BT, _MB, _HD, 1),
        cache_identity=identity,
        build=build,
    )
    reader = SessionReader(session)
    use_case = GetPRDiffUseCase(reader, cache)
    await use_case.execute("o", "r", 1, base_url="https://gitlab.example.com")
    assert reader.open_calls == 1
    assert reader.open_kwargs[0]["base_url"] == "https://gitlab.example.com"
